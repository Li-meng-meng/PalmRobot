namespace PalmRobot.Core.Transport;

/// <summary>
/// 传输层抽象：串口 / WebSocket / Mock 都实现它。
/// 上层只关心"发一行、收一行"。
/// </summary>
public interface ITransport : IDisposable
{
    bool IsConnected { get; }
    string Description { get; }

    /// <summary>收到一行（不含换行符，已去 \r）。事件在工作线程触发。</summary>
    event Action<string>? LineReceived;

    /// <summary>连接断开（主动关闭不触发）。</summary>
    event Action? Disconnected;

    Task ConnectAsync(CancellationToken ct = default);
    void Disconnect();
    void SendLine(string line);
}
