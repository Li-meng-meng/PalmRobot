using System.IO.Ports;
using System.Text;
using PalmRobot.Core.Transport;

namespace PalmRobot.App.Services;

/// <summary>串口传输（115200 8N1，\n 行分隔，忽略 \r）。</summary>
public sealed class SerialTransport : ITransport
{
    private readonly SerialPort _port;
    private readonly StringBuilder _lineBuffer = new();
    private CancellationTokenSource? _readCts;
    private Task? _readLoop;
    private volatile bool _closing;

    public SerialTransport(string portName, int baudRate = 115200)
    {
        _port = new SerialPort(portName, baudRate, Parity.None, 8, StopBits.One)
        {
            NewLine = "\n",
            ReadTimeout = 50,
            WriteTimeout = 500,
        };
    }

    public bool IsConnected => _port.IsOpen;
    public string Description => $"串口 {_port.PortName} @ {_port.BaudRate}";

    public event Action<string>? LineReceived;
    public event Action? Disconnected;

    public Task ConnectAsync(CancellationToken ct = default)
    {
        if (_port.IsOpen) return Task.CompletedTask;
        _port.Open();
        _port.DiscardInBuffer();
        _readCts = new CancellationTokenSource();
        _readLoop = Task.Run(() => ReadLoopAsync(_readCts.Token));
        return Task.CompletedTask;
    }

    public void Disconnect()
    {
        if (!_port.IsOpen) return;
        _closing = true;
        _readCts?.Cancel();
        try { _readLoop?.Wait(200); } catch { /* 忽略 */ }
        try { _port.Close(); } catch { /* 忽略 */ }
        _readCts?.Dispose();
        _readCts = null;
        _readLoop = null;
        _closing = false;
    }

    public void SendLine(string line)
    {
        if (!_port.IsOpen) return;
        try
        {
            _port.Write(line + "\n");
        }
        catch (Exception ex)
        {
            Services.LogService.Error($"串口写入失败: {ex.Message}");
        }
    }

    private async Task ReadLoopAsync(CancellationToken ct)
    {
        var buffer = new byte[512];
        var decoder = Encoding.ASCII.GetDecoder();
        try
        {
            while (!ct.IsCancellationRequested && _port.IsOpen)
            {
                var n = await _port.BaseStream.ReadAsync(buffer, ct);
                if (n <= 0) continue;

                var chars = new char[decoder.GetCharCount(buffer, 0, n, false)];
                decoder.GetChars(buffer, 0, n, chars, 0, false);

                foreach (var ch in chars)
                {
                    if (ch == '\n')
                    {
                        var line = _lineBuffer.ToString();
                        _lineBuffer.Clear();
                        if (line.Length > 0)
                            LineReceived?.Invoke(line.TrimEnd('\r'));
                    }
                    else
                    {
                        _lineBuffer.Append(ch);
                        // 防御：异常超长行丢弃
                        if (_lineBuffer.Length > 4096) _lineBuffer.Clear();
                    }
                }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception)
        {
            if (!_closing) Disconnected?.Invoke();
        }
    }

    public void Dispose() => Disconnect();
}
