using System.Collections.ObjectModel;
using System.IO;
using System.IO.Ports;
using System.Windows;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using PalmRobot.Core;
using PalmRobot.Core.Protocol;
using PalmRobot.Core.Transport;
using PalmRobot.App.Services;

namespace PalmRobot.App.ViewModels;

/// <summary>设备列表项。</summary>
public sealed class DeviceItem
{
    public DeviceItem(string name, string kind, ITransport? transportPrototype)
    {
        Name = name;
        Kind = kind;
    }
    public string Name { get; }
    public string Kind { get; }
    public bool IsMock => Kind == "模拟";
    public bool IsSim => Kind == "仿真";
    public string Display => IsMock ? $"{Name}（内置仿真，无需硬件）"
        : IsSim ? $"{Name}（MuJoCo 物理仿真，本机 8555/8556）"
        : $"{Name}　{Kind}";
}

/// <summary>单个舵机通道（滑块）。</summary>
public partial class ServoChannelViewModel : ObservableObject
{
    public ServoChannelViewModel(int index)
    {
        Index = index;
        Joint = ChannelMap.JointNames[index];
        Part = ChannelMap.JointParts[index];
        Min = ChannelMap.Limits[index].Min;
        Max = ChannelMap.Limits[index].Max;
        Angle = ChannelMap.StandPose[index];
    }

    public int Index { get; }
    public string Joint { get; }
    public string Part { get; }
    public int Min { get; }
    public int Max { get; }
    public string Header => $"ch{Index} · {Joint} · {Part}";

    [ObservableProperty]
    private int _angle;

    /// <summary>角度变化（用户拖动或程序设置）。</summary>
    public event Action? AngleChanged;

    partial void OnAngleChanged(int value) => AngleChanged?.Invoke();
}

/// <summary>命令行条目。</summary>
public sealed record ConsoleEntry(string Direction, string Line, bool IsError);

/// <summary>主视图模型。</summary>
public partial class MainViewModel : ObservableObject
{
    private RobotClient? _client;
    private ITransport? _transport;
    private CancellationTokenSource? _pollCts;
    private readonly System.Timers.Timer _uiTimer;          // 5Hz 显示刷新
    private volatile RobotStatus? _latestStatus;
    private volatile bool _frameDirty;
    private long _suppressFrameSendUntil;                    // 姿态指令后暂缓 FRAME
    private int _consecutivePollFailures;
    private string[] _lastSerialPorts = Array.Empty<string>();
    private readonly SimVideoClient _simVideo = new();       // MuJoCo 视频流
    private System.Diagnostics.Process? _simProcess;         // 拉起的 sim_server.py

    /// <summary>仿真视频流收到一帧 JPEG（后台线程）。</summary>
    public event Action<byte[]>? SimFrameReceived;

    /// <summary>视频流状态文本（连接/断开提示，供画面标签页显示）。</summary>
    public void WatchSimVideoStatus(Action<string> sink) => _simVideo.StatusChanged += sink;

    // —— 视频转动作（导入视频 → 生成 .motion → 3D 预览 / 上传设备） ——

    [ObservableProperty] private string _videoPath = string.Empty;
    [ObservableProperty] private bool _genModeL1;            // L1 律动（音频 BPM）
    [ObservableProperty] private bool _genModeL2 = true;     // L2 姿态重定向（默认）
    [ObservableProperty] private string _videoResultText = "未生成";

    private string? _generatedJson;                          // 生成的 .motion 原文
    private string _generatedName = "";
    private bool _generating;

    partial void OnGenModeL2Changed(bool value) { if (value) _genModeL1 = false; }
    partial void OnGenModeL1Changed(bool value) { if (value) _genModeL2 = false; }

    /// <summary>生成了新动作（3D 预览联动播放）。</summary>
    public event Action<string>? MotionJsonGenerated;

    [RelayCommand]
    private void PickVideo()
    {
        var dlg = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择视频",
            Filter = "视频文件|*.mp4;*.mov;*.avi;*.webm;*.m4v|所有文件|*.*",
        };
        if (dlg.ShowDialog() == true) VideoPath = dlg.FileName;
    }

    [RelayCommand(CanExecute = nameof(CanGenerate))]
    private async Task GenerateVideoMotionAsync()
    {
        if (string.IsNullOrWhiteSpace(VideoPath) || !File.Exists(VideoPath))
        {
            LogService.Warn("请先选择有效的视频文件");
            return;
        }
        var aiDir = FindAiDir();
        var simDir = FindSimDir();
        if (aiDir is null || simDir is null)
        {
            LogService.Error("未找到 software/ai 脚本或 simulation venv");
            return;
        }

        // 动作名：视频文件名清洗（协议限制 ≤32 字符）
        var sb = new System.Text.StringBuilder();
        foreach (var c in Path.GetFileNameWithoutExtension(VideoPath))
            sb.Append(char.IsAsciiLetterOrDigit(c) ? c : '_');
        _generatedName = sb.ToString().Trim('_');
        if (_generatedName.Length == 0) _generatedName = "video_motion";
        if (_generatedName.Length > 32) _generatedName = _generatedName[..32];

        var python = Path.Combine(simDir, ".venv", "Scripts", "python.exe");
        var script = Path.Combine(aiDir, GenModeL2 ? "pose_to_motion.py" : "video_to_motion.py");
        var outPath = Path.Combine(aiDir, "out", $"{_generatedName}.motion");
        Directory.CreateDirectory(Path.GetDirectoryName(outPath)!);

        _generating = true;
        GenerateVideoMotionCommand.NotifyCanExecuteChanged();
        VideoResultText = "生成中…（L2 需逐帧推理，请耐心等待；详见日志页）";
        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = python,
                Arguments = $"\"{script}\" \"{VideoPath}\" \"{outPath}\"",
                WorkingDirectory = aiDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = System.Text.Encoding.UTF8,
                StandardErrorEncoding = System.Text.Encoding.UTF8,
            };
            psi.Environment["PYTHONIOENCODING"] = "utf-8";
            var proc = System.Diagnostics.Process.Start(psi)
                ?? throw new InvalidOperationException("无法启动 Python 进程");
            proc.OutputDataReceived += (_, e) => { if (e.Data is not null) LogService.Info($"[ai] {e.Data}"); };
            proc.ErrorDataReceived += (_, e) => { if (e.Data is not null) LogService.Warn($"[ai] {e.Data}"); };
            proc.BeginOutputReadLine();
            proc.BeginErrorReadLine();
            await proc.WaitForExitAsync();

            if (proc.ExitCode != 0 || !File.Exists(outPath))
            {
                VideoResultText = "✗ 生成失败（详见日志页）";
                return;
            }

            // 统一动作名（预览显示与上传同名）
            var json = await File.ReadAllTextAsync(outPath);
            System.Text.Json.Nodes.JsonObject? node = null;
            if (System.Text.Json.Nodes.JsonNode.Parse(json) is System.Text.Json.Nodes.JsonObject obj)
            {
                node = obj;
                node["name"] = _generatedName;
                json = node.ToJsonString();
            }
            _generatedJson = json;
            VideoResultText = $"✓ 生成成功：{_generatedName}（{node?["duration_ms"]} ms）→ {outPath}";
            MotionJsonGenerated?.Invoke(_generatedJson);
            LogService.Info($"动作已生成并在 3D 预览播放：{_generatedName}");
        }
        catch (Exception ex)
        {
            VideoResultText = $"✗ 生成失败：{ex.Message}";
            LogService.Error($"视频转动作失败: {ex.Message}");
        }
        finally
        {
            _generating = false;
            GenerateVideoMotionCommand.NotifyCanExecuteChanged();
        }
    }

    private bool CanGenerate() => !_generating;

    /// <summary>上传动作包到当前连接的设备并播放（仿真机器人 = 虚拟引擎；真机 = USB）。</summary>
    [RelayCommand]
    private async Task DeployGeneratedAsync()
    {
        if (_generatedJson is null)
        {
            LogService.Warn("请先生成动作");
            return;
        }
        if (_client is null || !_client.IsConnected)
        {
            LogService.Warn("请先连接设备（仿真机器人 / 模拟机器人 / 真机均可）");
            return;
        }
        try
        {
            var size = System.Text.Encoding.UTF8.GetByteCount(_generatedJson);
            // 提前挂监听：OK SAVED / ERR 不占请求配对
            var saved = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            void OnRaw(string line, bool outgoing)
            {
                if (outgoing) return;
                if (line.StartsWith("OK SAVED")) saved.TrySetResult(true);
                else if (line.StartsWith("ERR ")) saved.TrySetResult(false);
            }
            _client.RawLine += OnRaw;
            try
            {
                var resp = await _client.SendAsync(RobotCommand.Putm(_generatedName, size));
                if (!resp.Success)
                {
                    LogService.Warn($"PUTM 失败: {resp.ErrorMessage ?? resp.ErrorCode.ToString()}");
                    return;
                }
                _transport!.SendLine(_generatedJson);
                if (!await saved.Task.WaitAsync(TimeSpan.FromSeconds(5)))
                {
                    LogService.Warn("动作包上传被拒（ERR），详见日志页");
                    return;
                }
            }
            finally { _client.RawLine -= OnRaw; }

            var play = await _client.SendAsync(RobotCommand.Play(_generatedName));
            LogService.Info(play.Success
                ? $"动作包已上传并开始播放：{_generatedName}（{size} B）"
                : $"上传成功但播放失败: {play.ErrorMessage ?? play.ErrorCode.ToString()}");
        }
        catch (Exception ex)
        {
            LogService.Error($"下发失败: {ex.Message}");
        }
    }

    /// <summary>从 exe 目录向上找 software/ai。</summary>
    private static string? FindAiDir()
    {
        for (var d = new DirectoryInfo(AppContext.BaseDirectory); d is not null; d = d.Parent)
        {
            var cand = Path.Combine(d.FullName, "software", "ai");
            if (File.Exists(Path.Combine(cand, "video_to_motion.py"))) return cand;
        }
        return null;
    }

    public MainViewModel()
    {
        Devices.Add(new DeviceItem("模拟机器人", "模拟", null));
        Devices.Add(new DeviceItem("仿真机器人", "仿真", null));
        ScanSerialPorts();
        for (var i = 0; i < 8; i++)
        {
            var ch = new ServoChannelViewModel(i);
            ch.AngleChanged += OnServoAngleChanged;
            ServoChannels.Add(ch);
        }
        _uiTimer = new System.Timers.Timer(200) { SynchronizingObject = null };
        _uiTimer.Elapsed += (_, _) => OnUiTick();
        _uiTimer.Start();
        _simVideo.FrameReceived += jpeg => SimFrameReceived?.Invoke(jpeg);
    }

    // —— 设备 ——
    public ObservableCollection<DeviceItem> Devices { get; } = new();
    public ObservableCollection<ServoChannelViewModel> ServoChannels { get; } = new();
    public ObservableCollection<ConsoleEntry> ConsoleLines { get; } = new();
    public ObservableCollection<string> LogLines { get; } = new();

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(ConnectCommand))]
    private DeviceItem? _selectedDevice;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(DisconnectCommand))]
    private bool _isConnected;

    // —— 状态栏 ——
    [ObservableProperty] private string _connectionText = "未连接";
    [ObservableProperty] private string _firmwareVersion = "—";
    [ObservableProperty] private string _voltageText = "--.- V";
    [ObservableProperty] private string _temperatureText = "-- °C";
    [ObservableProperty] private string _batteryText = "--%";
    [ObservableProperty] private string _poseText = "—";
    [ObservableProperty] private string _errorText = "正常";
    [ObservableProperty] private string _frameRateText = "0 fps";

    private int _frameCountWindow;

    [ObservableProperty]
    private string _consoleInput = string.Empty;

    // —— 命令 ——

    [RelayCommand]
    private void Scan()
    {
        ScanSerialPorts();
        LogService.Info("已刷新设备列表");
    }

    [RelayCommand(CanExecute = nameof(CanConnect))]
    private async Task ConnectAsync()
    {
        if (SelectedDevice is null) return;
        try
        {
            ITransport transport;
            if (SelectedDevice.IsMock)
            {
                transport = new MockRobotTransport();
            }
            else if (SelectedDevice.IsSim)
            {
                transport = await ConnectSimAsync();
            }
            else
            {
                transport = new SerialTransport(SelectedDevice.Name);
            }

            await transport.ConnectAsync();
            _transport = transport;
            _client = new RobotClient(transport);
            _client.RawLine += OnRawLine;
            _client.StatusReceived += OnStatusReceived;
            _client.EventReceived += OnEventReceived;
            _client.UnsolicitedResponse += OnUnsolicited;
            transport.Disconnected += OnTransportDisconnected;

            var ver = await _client.SendAsync(RobotCommand.Ver());
            FirmwareVersion = ver.Success && !string.IsNullOrEmpty(ver.Data) ? ver.Data : "未知";

            IsConnected = true;
            ConnectionText = $"已连接 · {transport.Description}";
            LogService.Info($"连接成功: {transport.Description}（固件 {FirmwareVersion}）");
            StartPolling();
        }
        catch (Exception ex)
        {
            LogService.Error($"连接失败: {ex.Message}");
            ConnectionText = $"连接失败 · {ex.Message}";
            CleanupConnection();
        }
    }

    private bool CanConnect() => !IsConnected && SelectedDevice is not null;

    // —— 仿真机器人（MuJoCo 物理仿真） ——

    private const string SimHost = "127.0.0.1";
    private const int SimCmdPort = 8555;
    private const int SimVideoPort = 8556;

    /// <summary>连接仿真服务；未运行则自动拉起 sim_server.py 再重试。</summary>
    private async Task<ITransport> ConnectSimAsync()
    {
        var transport = new TcpTransport(SimHost, SimCmdPort);
        try
        {
            await transport.ConnectAsync();
        }
        catch
        {
            transport.Dispose();
            LogService.Info("仿真服务未运行，正在拉起 simulation/sim_server.py…");
            StartSimServer();
            TcpTransport? retry = null;
            for (var i = 0; i < 24; i++)   // 模型加载 + 端口就绪最多等 12 s
            {
                await Task.Delay(500);
                retry = new TcpTransport(SimHost, SimCmdPort);
                try
                {
                    await retry.ConnectAsync();
                    break;
                }
                catch
                {
                    retry.Dispose();
                    retry = null;
                }
            }
            if (retry is null)
                throw new TimeoutException("仿真服务启动超时，请手动运行 simulation/sim_server.py 查看报错");
            transport = retry;
        }

        // 指令通道已通，再连视频流（失败只提示，不阻塞连接）
        try
        {
            await _simVideo.ConnectAsync(SimHost, SimVideoPort);
        }
        catch (Exception ex)
        {
            LogService.Warn($"视频流连接失败（指令通道可用）: {ex.Message}");
        }
        return transport;
    }

    private void StartSimServer()
    {
        if (_simProcess is { HasExited: false }) return;
        var simDir = FindSimDir();
        if (simDir is null) throw new DirectoryNotFoundException("未找到 software/simulation/sim_server.py");
        var python = Path.Combine(simDir, ".venv", "Scripts", "python.exe");
        var script = Path.Combine(simDir, "sim_server.py");
        if (!File.Exists(python) || !File.Exists(script))
            throw new FileNotFoundException("缺少 simulation/.venv/Scripts/python.exe 或 sim_server.py");

        var psi = new System.Diagnostics.ProcessStartInfo
        {
            FileName = python,
            Arguments = $"\"{script}\"",
            WorkingDirectory = simDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        var proc = System.Diagnostics.Process.Start(psi)
            ?? throw new InvalidOperationException("无法启动 Python 进程");
        _simProcess = proc;
        proc.OutputDataReceived += (_, e) => { if (e.Data is not null) LogService.Info($"[sim] {e.Data}"); };
        proc.ErrorDataReceived += (_, e) => { if (e.Data is not null) LogService.Warn($"[sim] {e.Data}"); };
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();
        LogService.Info($"仿真服务进程已启动 PID={proc.Id}");
    }

    /// <summary>从 exe 目录向上找 software/simulation。</summary>
    private static string? FindSimDir()
    {
        for (var d = new DirectoryInfo(AppContext.BaseDirectory); d is not null; d = d.Parent)
        {
            var cand = Path.Combine(d.FullName, "software", "simulation");
            if (File.Exists(Path.Combine(cand, "sim_server.py"))) return cand;
        }
        return null;
    }

    /// <summary>应用退出时收尾（断开视频流、结束拉起的仿真进程）。</summary>
    public void ShutdownSimServer()
    {
        _simVideo.Disconnect();
        if (_simProcess is { HasExited: false })
        {
            try { _simProcess.Kill(true); } catch { /* 忽略 */ }
        }
        _simProcess = null;
    }

    [RelayCommand(CanExecute = nameof(CanDisconnect))]
    private void Disconnect()
    {
        LogService.Info("主动断开连接");
        CleanupConnection();
    }

    private bool CanDisconnect() => IsConnected;

    private void OnTransportDisconnected()
    {
        LogService.Warn("设备连接断开");
        Application.Current?.Dispatcher.BeginInvoke(() =>
        {
            CleanupConnection();
            ConnectionText = "设备已断开";
        });
    }

    private void CleanupConnection()
    {
        _pollCts?.Cancel();
        _pollCts = null;
        _client?.Dispose();
        _transport?.Disconnect();
        _transport?.Dispose();
        _client = null;
        _transport = null;
        _simVideo.Disconnect();   // 仿真服务进程保留运行，便于重连
        IsConnected = false;
        ConnectionText = "未连接";
        FirmwareVersion = "—";
        DisconnectCommand.NotifyCanExecuteChanged();
        ConnectCommand.NotifyCanExecuteChanged();
    }

    private void StartPolling()
    {
        _pollCts = new CancellationTokenSource();
        var ct = _pollCts.Token;
        _ = Task.Run(async () =>
        {
            while (!ct.IsCancellationRequested && _client is { IsConnected: true })
            {
                var resp = await _client.SendAsync(RobotCommand.Status(), TimeSpan.FromSeconds(2));
                if (!resp.Success && resp.LocalError == LocalError.Timeout)
                {
                    _consecutivePollFailures++;
                    if (_consecutivePollFailures >= 3)
                    {
                        LogService.Error("连续 3 次状态查询超时，判定设备离线");
                        Application.Current?.Dispatcher.BeginInvoke(CleanupConnection);
                        return;
                    }
                }
                else
                {
                    _consecutivePollFailures = 0;
                }
                await Task.Delay(1000, ct).ContinueWith(_ => { });
            }
        }, ct);
    }

    // —— 姿态与动作 ——

    [RelayCommand]
    private async Task PoseAsync(string name)
    {
        if (!await EnsureConnected()) return;
        SuppressFrameSend(600);
        SetSlidersToPose(name);
        var resp = await _client!.SendAsync(RobotCommand.Pose(name));
        LogResponse(resp);
        if (name is ChannelMap.PoseWave or ChannelMap.PoseDance)
            MotionRequested?.Invoke(name);
    }

    [RelayCommand]
    private async Task StopAsync()
    {
        if (!await EnsureConnected()) return;
        SuppressFrameSend(600);
        var resp = await _client!.SendAsync(RobotCommand.Stop());
        LogResponse(resp);
        MotionRequested?.Invoke(null);
    }

    [RelayCommand]
    private async Task FacePresetAsync(string preset)
    {
        if (!await EnsureConnected()) return;
        var resp = await _client!.SendAsync(RobotCommand.FacePreset(preset));
        LogResponse(resp);
    }

    /// <summary>FRAME 角度已发出（WebView2 3D 预览订阅同步）。</summary>
    public event Action<int[]>? FrameSent;

    [RelayCommand]
    private async Task PlayAsync(string name)
    {
        if (!await EnsureConnected()) return;
        SuppressFrameSend(600);
        var resp = await _client!.SendAsync(RobotCommand.Play(name));
        LogResponse(resp);
        var json = TryReadMotionJson(name);
        if (json is not null) MotionJsonGenerated?.Invoke(json);
    }

    /// <summary>从 exe 目录向上找 software/tools/motions/&lt;name&gt;.motion 读动作包。</summary>
    private static string? TryReadMotionJson(string name)
    {
        for (var d = new DirectoryInfo(AppContext.BaseDirectory); d is not null; d = d.Parent)
        {
            var f = Path.Combine(d.FullName, "tools", "motions", $"{name}.motion");
            if (File.Exists(f)) return File.ReadAllText(f);
        }
        return null;
    }

    /// <summary>动作类 POSE（wave/dance）已发出，null 表示 STOP（3D 预览联动播放/停止）。</summary>
    public event Action<string?>? MotionRequested;

    private void SetSlidersToPose(string name)
    {
        var pose = name switch
        {
            ChannelMap.PoseStand => ChannelMap.StandPose,
            ChannelMap.PoseSit => ChannelMap.SitPose,
            ChannelMap.PoseSleep => ChannelMap.SleepPose,
            _ => null,
        };
        if (pose is null) return;
        for (var i = 0; i < 8; i++) ServoChannels[i].Angle = pose[i];
        FrameSent?.Invoke(pose);
    }

    // —— 舵机滑块 → FRAME 节流（5Hz） ——

    private void OnServoAngleChanged()
    {
        if (Environment.TickCount64 < _suppressFrameSendUntil) return;
        _frameDirty = true;
    }

    private void SendFrameIfDirty()
    {
        if (!_frameDirty || _client is not { IsConnected: true }) return;
        _frameDirty = false;
        var angles = ServoChannels.Select(c => c.Angle).ToArray();
        _ = SendFrameInternalAsync(angles);
    }

    private async Task SendFrameInternalAsync(int[] angles)
    {
        try
        {
            Interlocked.Increment(ref _frameCountWindow);
            var resp = await _client!.SendAsync(RobotCommand.Frame(0, angles));
            if (resp.Success) FrameSent?.Invoke(angles);
            else LogService.Warn($"FRAME 失败: {resp.ErrorMessage ?? resp.ErrorCode.ToString()}");
        }
        catch (ObjectDisposedException) { }
    }

    private void SuppressFrameSend(int ms) => _suppressFrameSendUntil = Environment.TickCount64 + ms;

    // —— 命令行 ——

    [RelayCommand]
    private async Task SendConsoleAsync()
    {
        var input = ConsoleInput?.Trim();
        if (string.IsNullOrEmpty(input) || !await EnsureConnected()) return;
        ConsoleInput = string.Empty;
        AppendConsole("→", input, false);
        var resp = await _client!.SendAsync(input, timeout: TimeSpan.FromSeconds(3));
        var reply = resp.RawLine.Length > 0 ? resp.RawLine
                  : resp.LocalError == LocalError.Timeout ? "# 超时" : "# 无应答";
        AppendConsole("←", reply, !resp.Success);
    }

    private void AppendConsole(string dir, string line, bool isError)
    {
        Application.Current?.Dispatcher.BeginInvoke(() =>
        {
            ConsoleLines.Add(new ConsoleEntry(dir, line, isError));
            while (ConsoleLines.Count > 500) ConsoleLines.RemoveAt(0);
        });
    }

    // —— 事件处理 ——

    private void OnRawLine(string line, bool isOutgoing) =>
        LogService.Write(LogLevel.Raw, $"{(isOutgoing ? ">>> " : "<<< ")}{line}");

    private void OnStatusReceived(RobotStatus status) => _latestStatus = status;

    private void OnEventReceived(EvtLine evt)
    {
        switch (evt.Kind)
        {
            case "SAFE":
                LogService.Warn($"保护触发: {evt.Rest}，已停止运动");
                break;
            case "WIFI":
                LogService.Info($"配网结果: {evt.Rest}");
                break;
            case "PLAY":
                LogService.Info($"动作播放完成: {evt.Rest}");
                break;
        }
    }

    private void OnUnsolicited(LinePayload payload) =>
        LogService.Warn($"收到未配对应答: {payload}");

    private void LogResponse(RobotResponse resp)
    {
        if (!resp.Success)
            LogService.Warn($"指令失败: {resp.ErrorMessage ?? resp.ErrorCode.ToString()}");
    }

    private async Task<bool> EnsureConnected()
    {
        if (_client is { IsConnected: true }) return true;
        LogService.Warn("未连接设备");
        return false;
    }

    // —— 5Hz UI 刷新 ——

    private void OnUiTick()
    {
        Application.Current?.Dispatcher.BeginInvoke(() =>
        {
            var st = _latestStatus;
            if (st is not null)
            {
                VoltageText = $"{st.Voltage:F1} V";
                TemperatureText = $"{st.TemperatureC} °C";
                BatteryText = $"{st.BatteryPercent}%";
                PoseText = st.Pose;
                ErrorText = st.LastError == RobotErrorCode.OK ? "正常" : $"{(int)st.LastError} {st.LastError}";
            }
            SendFrameIfDirty();
            FrameRateText = $"{System.Threading.Volatile.Read(ref _frameCountWindow) * 5} fps";
            Interlocked.Exchange(ref _frameCountWindow, 0);
        });
    }

    private void ScanSerialPorts()
    {
        var ports = SafePortNames();
        _lastSerialPorts = ports;
        // 保留内置设备（模拟 / 仿真）与仍存在的串口
        for (var i = Devices.Count - 1; i >= 0; i--)
            if (!Devices[i].IsMock && !Devices[i].IsSim && !ports.Contains(Devices[i].Name))
                Devices.RemoveAt(i);
        foreach (var p in ports)
            if (!Devices.Any(d => d.Name == p)) Devices.Add(new DeviceItem(p, "USB 串口", null));
        if (Devices.Count > 0 && SelectedDevice is null)
            SelectedDevice = Devices[0];
    }

    private static string[] SafePortNames()
    {
        try { return SerialPort.GetPortNames().OrderBy(p => p).ToArray(); }
        catch { return Array.Empty<string>(); }
    }
}
