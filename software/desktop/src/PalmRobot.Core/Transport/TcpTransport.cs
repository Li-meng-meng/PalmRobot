using System.Net.Sockets;
using System.Text;

namespace PalmRobot.Core.Transport;

/// <summary>
/// TCP 行协议传输（连接本机 MuJoCo 物理仿真服务 sim_server.py）。
/// 行为对齐串口：一行一条（\n 结尾），连接后先收 READY 横幅，严格请求-应答。
/// </summary>
public sealed class TcpTransport : ITransport
{
    private readonly string _host;
    private readonly int _port;
    private TcpClient? _client;
    private NetworkStream? _stream;
    private CancellationTokenSource? _cts;

    public TcpTransport(string host, int port)
    {
        _host = host;
        _port = port;
    }

    public bool IsConnected { get; private set; }
    public string Description => $"MuJoCo 物理仿真（{_host}:{_port}）";

    public event Action<string>? LineReceived;
    public event Action? Disconnected;

    public async Task ConnectAsync(CancellationToken ct = default)
    {
        if (IsConnected) return;   // 幂等（与 MockRobotTransport 行为一致）
        var client = new TcpClient();
        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(TimeSpan.FromSeconds(3));
        await client.ConnectAsync(_host, _port, timeoutCts.Token);   // 失败抛异常，由上层提示
        _client = client;
        _stream = client.GetStream();
        IsConnected = true;
        _cts = new CancellationTokenSource();
        _ = Task.Run(() => ReadLoopAsync(_cts.Token));
    }

    private async Task ReadLoopAsync(CancellationToken ct)
    {
        var buffer = new byte[4096];
        var pending = new StringBuilder();
        try
        {
            var stream = _stream!;
            while (!ct.IsCancellationRequested && IsConnected)
            {
                var n = await stream.ReadAsync(buffer, ct);
                if (n == 0) break;   // 对端关闭
                pending.Append(Encoding.UTF8.GetString(buffer, 0, n));
                while (true)   // 按行切分，不完整的行留到下次
                {
                    var text = pending.ToString();
                    var idx = text.IndexOf('\n');
                    if (idx < 0) break;
                    var line = text[..idx].TrimEnd('\r');
                    pending.Clear();
                    pending.Append(text[(idx + 1)..]);
                    if (line.Length > 0) LineReceived?.Invoke(line);
                }
            }
        }
        catch (OperationCanceledException) { return; }
        catch (Exception) { /* 网络异常 → 走断线流程 */ }

        if (!IsConnected) return;   // 主动断开不触发事件
        IsConnected = false;
        Disconnected?.Invoke();
    }

    public void SendLine(string line)
    {
        if (!IsConnected || _stream is null) return;
        try
        {
            var bytes = Encoding.UTF8.GetBytes(line + "\n");
            _stream.Write(bytes);
        }
        catch { /* 写失败由读循环统一判定离线 */ }
    }

    public void Disconnect()
    {
        if (!IsConnected && _client is null) return;
        IsConnected = false;
        _cts?.Cancel();
        try { _client?.Close(); } catch { /* 忽略 */ }
        _client = null;
        _stream = null;
    }

    public void Dispose() => Disconnect();
}
