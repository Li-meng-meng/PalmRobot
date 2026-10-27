﻿﻿using System.Text.Json;
using System.Threading.Channels;
using PalmRobot.Core.Protocol;

namespace PalmRobot.Core.Transport;

/// <summary>
/// 进程内模拟固件（无硬件开发用）。行为对齐 docs/20：READY 横幅、逐条应答、
/// FRAMES 批量模式、PUTM 数据流、EVT WIFI。应答带 10–40 ms 模拟时延，顺序保证。
/// </summary>
public sealed class MockRobotTransport : ITransport
{
    private readonly Channel<string> _inbox = Channel.CreateUnbounded<string>(new UnboundedChannelOptions
    {
        SingleReader = true,
    });
    private readonly int[] _angles = { 135, 45, 45, 135, 0, 180, 0, 180 };
    private readonly int[] _calMid = Enumerable.Repeat(90, 8).ToArray();
    private readonly Dictionary<string, string> _motions = new();
    private readonly Dictionary<string, string> _cfg = new()
    {
        ["gait.stride"] = "30", ["gait.freq"] = "2.0", ["gait.lift"] = "25",
        ["face.bright"] = "80", ["face.exagg"] = "150",
        ["safe.batt_low"] = "20", ["safe.cliff"] = "1", ["safe.stall_ms"] = "500",
        ["servo.map"] = "0,1,2,3,4,5,6,7", ["report"] = "1",
    };

    private Task? _worker;
    private CancellationTokenSource? _cts;
    private double _voltage = 12.4;
    private int _temp = 42;
    private int _batt = 80;
    private string _pose = "stand";
    private int _framesRemaining;   // FRAMES 批量模式剩余帧数
    private int _framesTotal;
    private bool _putmPending;

    public string Firmware { get; set; } = "v0.1.0-mock";
    public bool IsConnected { get; private set; }
    public string Description => "模拟机器人（内置固件仿真）";

    public event Action<string>? LineReceived;
    public event Action? Disconnected;

    public Task ConnectAsync(CancellationToken ct = default)
    {
        if (IsConnected) return Task.CompletedTask;
        IsConnected = true;
        _cts = new CancellationTokenSource();
        _worker = Task.Run(() => WorkLoopAsync(_cts.Token), ct);
        // 上电横幅（延迟确保上层已订阅事件）
        _ = Task.Run(async () =>
        {
            await Task.Delay(80, ct);
            LineReceived?.Invoke($"READY {Firmware}");
        }, ct);
        return Task.CompletedTask;
    }

    public void Disconnect()
    {
        if (!IsConnected) return;
        IsConnected = false;
        _cts?.Cancel();
        try { _worker?.Wait(100); } catch { /* 忽略 */ }
        _cts?.Dispose();
        _cts = null;
        _worker = null;
    }

    public void SendLine(string line) => _inbox.Writer.TryWrite(line);

    public void Dispose() => Disconnect();

    // —— 工作循环：保证应答顺序 ——

    private async Task WorkLoopAsync(CancellationToken ct)
    {
        try
        {
            await foreach (var line in _inbox.Reader.ReadAllAsync(ct))
            {
                await Task.Delay(Random.Shared.Next(10, 40), ct);   // 模拟固件处理时延
                var responses = Handle(line);
                foreach (var (text, delayMs) in responses)
                {
                    if (delayMs > 0) await Task.Delay(delayMs, ct);
                    LineReceived?.Invoke(text);   // worker 循环内顺序投递，保证多行应答 FIFO
                }
            }
        }
        catch (OperationCanceledException) { }
    }

    // —— 指令处理 ——

    private List<(string Line, int DelayMs)> Handle(string raw)
    {
        var results = new List<(string, int)>();
        var line = raw.TrimEnd('\r', '\n').Trim();
        if (line.Length == 0) return results;

        // PUTM 数据体：一行 JSON
        if (_putmPending)
        {
            _putmPending = false;
            results.Add(HandlePutmData(line));
            return results;
        }

        var tokens = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        var cmd = tokens[0].ToUpperInvariant();

        switch (cmd)
        {
            case "MOVE":
                if (tokens.Length == 3 && TryInt(tokens[1], out var v) && TryInt(tokens[2], out var w)
                    && v is >= -100 and <= 100 && w is >= -100 and <= 100)
                {
                    _pose = "motion";
                    results.Add(("OK", 0));
                }
                else results.Add(Err("1 BAD_ARGS"));
                break;

            case "STOP":
                _pose = "stand";
                results.Add(("OK", 0));
                break;

            case "POSE":
                if (tokens.Length == 2 && tokens[1] is ChannelMap.PoseStand or ChannelMap.PoseSit
                    or ChannelMap.PoseWave or ChannelMap.PoseDance or ChannelMap.PoseSleep)
                {
                    _pose = tokens[1] is ChannelMap.PoseWave or ChannelMap.PoseDance ? "motion" : tokens[1];
                    if (tokens[1] == ChannelMap.PoseStand) Array.Copy(ChannelMap.StandPose, _angles, 8);
                    results.Add(("OK", 0));
                }
                else results.Add(Err("1 BAD_ARGS"));
                break;

            case "PLAY":
                if (tokens.Length == 2 && (_motions.ContainsKey(tokens[1])
                    || (tokens[1].Length == 1 && char.IsDigit(tokens[1][0]))))
                {
                    _pose = "motion";
                    results.Add(("OK", 0));
                }
                else if (tokens.Length == 2 && tokens[1] == "loop")
                {
                    _pose = "motion";
                    results.Add(("OK", 0));
                }
                else results.Add(Err("8 NOT_FOUND"));
                break;

            case "FRAME":
                if (_framesRemaining > 0)
                {
                    // 批量模式内
                    if (TryParseFrame(tokens, out var batchAngles))
                    {
                        Array.Copy(batchAngles, _angles, 8);
                        _framesRemaining--;
                        results.Add(_framesRemaining == 0 ? ("OK DONE", 0) : ("OK", 0));
                    }
                    else results.Add(Err("1 BAD_ARGS"));
                }
                else if (TryParseFrame(tokens, out var singleAngles))
                {
                    Array.Copy(singleAngles, _angles, 8);
                    _pose = "motion";
                    results.Add(("OK", 0));
                }
                else results.Add(Err("1 BAD_ARGS"));
                break;

            case "FRAMES":
                if (tokens.Length == 2 && TryInt(tokens[1], out var n) && n is >= 0 and <= 100000)
                {
                    _framesRemaining = n;
                    _framesTotal = n;
                    results.Add(("OK", 0));
                }
                else results.Add(Err("1 BAD_ARGS"));
                break;

            case "FACE":
                results.Add(HandleFace(tokens));
                break;

            case "STATUS":
                TickEnvironment();
                results.Add(($"OK {StatusJson()}", 0));
                break;

            case "PING":
                results.Add(("PONG", 0));
                break;

            case "VER":
                results.Add(($"OK {Firmware}", 0));
                break;

            case "CFG":
                results.Add(HandleCfg(tokens));
                break;

            case "CAL":
                results.Add(HandleCal(tokens));
                break;

            case "WIFI":
                if (tokens.Length == 3)
                {
                    results.Add(("OK", 0));
                    results.Add(("EVT WIFI 192.168.4.1", 400));   // 异步配网结果
                }
                else results.Add(Err("1 BAD_ARGS"));
                break;

            case "SAVE":
                results.Add(("OK", 0));
                break;

            case "REBOOT":
                results.Add(("OK", 0));
                results.Add(($"READY {Firmware}", 150));   // 重启横幅
                break;

            case "RESET":
                results.Add(("OK", 0));
                break;

            case "PUTM":
                if (tokens.Length == 3 && tokens[1].Length <= 32 && TryInt(tokens[2], out var size)
                    && size is >= 1 and <= 16384)
                {
                    _putmPending = true;
                    results.Add(("OK PUT_READY", 0));
                }
                else results.Add(Err("1 BAD_ARGS"));
                break;

            case "LISTM":
                foreach (var m in _motions) results.Add(($"OK {m.Key} {m.Value.Length}", 0));
                results.Add(("OK DONE", 0));
                break;

            case "DELM":
                if (tokens.Length == 2 && _motions.Remove(tokens[1])) results.Add(("OK", 0));
                else results.Add(Err("8 NOT_FOUND"));
                break;

            default:
                results.Add(Err("2 UNKNOWN_CMD"));
                break;
        }

        return results;
    }

    private (string, int) HandlePutmData(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind == JsonValueKind.Object
                && doc.RootElement.TryGetProperty("version", out var ver)
                && ver.ValueKind == JsonValueKind.Number && ver.GetInt32() == 1
                && doc.RootElement.TryGetProperty("name", out var name)
                && name.ValueKind == JsonValueKind.String)
            {
                _motions[name.GetString()!] = json;
                return ("OK SAVED", 0);
            }
            return Err("11 CHECKSUM");
        }
        catch (JsonException)
        {
            return Err("11 CHECKSUM");
        }
    }

    private (string, int) HandleFace(string[] tokens)
    {
        if (tokens.Length == 13)
        {
            for (var i = 1; i <= 12; i++)
            {
                if (!TryInt(tokens[i], out var val) || val < (i is 9 or 10 ? -100 : 0) || val > 100)
                    return Err("1 BAD_ARGS");
            }
            return ("OK", 0);
        }
        if (tokens.Length == 2 && tokens[1] is "happy" or "sad" or "angry" or "surprise" or "sleep" or "neutral")
            return ("OK", 0);
        return Err("1 BAD_ARGS");
    }

    private (string, int) HandleCfg(string[] tokens)
    {
        if (tokens.Length == 2 && tokens[1] == "GET")
            return ($"OK {JsonSerializer.Serialize(_cfg)}", 0);
        if (tokens.Length == 3 && tokens[1] == "GET")
            return _cfg.TryGetValue(tokens[2], out var val) ? ($"OK {val}", 0) : Err("8 NOT_FOUND");
        if (tokens.Length == 4 && tokens[1] == "SET")
        {
            if (!_cfg.ContainsKey(tokens[2])) return Err("8 NOT_FOUND");
            _cfg[tokens[2]] = tokens[3];
            return ("OK", 0);
        }
        return Err("1 BAD_ARGS");
    }

    private (string, int) HandleCal(string[] tokens)
    {
        if (tokens.Length == 2 && tokens[1] == "SAVE") return ("OK", 0);
        if (tokens.Length == 2 && tokens[1] == "GET")
            return ($"OK {{\"mid\":[{string.Join(",", _calMid)}]}}", 0);
        if (tokens.Length == 3 && TryInt(tokens[1], out var ch) && ch is >= 0 and <= 7
            && TryInt(tokens[2], out var angle) && angle is >= 0 and <= 180)
        {
            _calMid[ch] = angle;
            return ("OK", 0);
        }
        return Err("1 BAD_ARGS");
    }

    private static bool TryParseFrame(string[] tokens, out int[] angles)
    {
        angles = new int[8];
        if (tokens.Length != 10 || !TryInt(tokens[1], out var t) || t is < 0 or > ChannelMap.MaxFrameT)
            return false;
        for (var i = 0; i < 8; i++)
        {
            if (!TryInt(tokens[i + 2], out var a) || a is < ChannelMap.MinAngle or > ChannelMap.MaxAngle)
                return false;
            angles[i] = a;
        }
        return true;
    }

    private void TickEnvironment()
    {
        // 随机游走模拟电压/温度漂移
        _voltage = Math.Clamp(_voltage + (Random.Shared.NextDouble() - 0.5) * 0.08, 11.6, 12.7);
        _temp = Math.Clamp(_temp + (Random.Shared.Next(3) - 1), 38, 55);
        _batt = Math.Clamp(_batt - (Random.Shared.Next(20) == 0 ? 1 : 0), 0, 100);
    }

    private string StatusJson() =>
        FormattableString.Invariant(
            $"{{\"v\":{_voltage:F1},\"t\":{_temp},\"batt\":{_batt},\"pose\":\"{_pose}\",\"err\":0}}");

    private static bool TryInt(string s, out int value) => int.TryParse(s, out value);

    private static (string Line, int DelayMs) Err(string msgAndCode) => ($"ERR {msgAndCode}", 0);
}
