using System.Net.Sockets;

namespace PalmRobot.App.Services;

/// <summary>
/// MuJoCo 仿真视频流客户端：tcp://127.0.0.1:8556，5 Hz JPEG。
/// 帧格式：4 字节大端长度前缀 + JPEG 字节（sim_server.py VideoHub）。
/// </summary>
public sealed class SimVideoClient : IDisposable
{
    private TcpClient? _client;
    private CancellationTokenSource? _cts;

    public bool IsConnected { get; private set; }

    /// <summary>收到一帧 JPEG（后台线程触发）。</summary>
    public event Action<byte[]>? FrameReceived;

    /// <summary>连接状态变化（可读文本）。</summary>
    public event Action<string>? StatusChanged;

    public async Task ConnectAsync(string host, int port)
    {
        Disconnect();
        var client = new TcpClient();
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        await client.ConnectAsync(host, port, cts.Token);
        _client = client;
        _cts = new CancellationTokenSource();
        IsConnected = true;
        StatusChanged?.Invoke($"视频流已连接 {host}:{port}");
        _ = Task.Run(() => ReadLoopAsync(_cts.Token));
    }

    private async Task ReadLoopAsync(CancellationToken ct)
    {
        var buf = new byte[65536];
        try
        {
            var stream = _client!.GetStream();
            while (!ct.IsCancellationRequested)
            {
                if (!await ReadExactAsync(stream, buf, 4, ct)) break;
                var len = (buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3];
                if (len is <= 0 or > 5_000_000) break;   // 长度异常，判定流损坏
                if (buf.Length < len) buf = new byte[len];
                if (!await ReadExactAsync(stream, buf, len, ct)) break;
                var jpeg = new byte[len];
                Buffer.BlockCopy(buf, 0, jpeg, 0, len);
                FrameReceived?.Invoke(jpeg);
            }
        }
        catch { /* 取消 / 断线 → 统一走离线流程 */ }
        IsConnected = false;
        StatusChanged?.Invoke("视频流断开");
    }

    private static async Task<bool> ReadExactAsync(NetworkStream s, byte[] buf, int n, CancellationToken ct)
    {
        var off = 0;
        while (off < n)
        {
            var k = await s.ReadAsync(buf.AsMemory(off, n - off), ct);
            if (k == 0) return false;
            off += k;
        }
        return true;
    }

    public void Disconnect()
    {
        _cts?.Cancel();
        try { _client?.Close(); } catch { /* 忽略 */ }
        _client = null;
        IsConnected = false;
    }

    public void Dispose() => Disconnect();
}
