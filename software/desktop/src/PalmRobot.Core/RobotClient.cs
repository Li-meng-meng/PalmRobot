using PalmRobot.Core.Protocol;
using PalmRobot.Core.Transport;

namespace PalmRobot.Core;

/// <summary>
/// 机器人客户端：严格请求-应答配对（docs/20 §9.1），事件分发，超时判定。
/// 同一时刻只允许一个在途指令；EVT 不占用应答配对。
/// </summary>
public sealed class RobotClient : IDisposable
{
    private readonly ITransport _transport;
    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private TaskCompletionSource<RobotResponse>? _pending;
    private bool _discardNextResponse;   // 超时后丢弃迟到的应答，防止错配
    private bool _disposed;

    public RobotClient(ITransport transport)
    {
        _transport = transport;
        _transport.LineReceived += OnLineReceived;
        _transport.Disconnected += () => Disconnected?.Invoke();
    }

    public bool IsConnected => _transport.IsConnected;
    public string TransportDescription => _transport.Description;

    /// <summary>所有原始行（请求与应答与事件），联调日志用。</summary>
    public event Action<string, bool>? RawLine;   // (line, isOutgoing)

    public event Action<EvtLine>? EventReceived;
    public event Action<RobotStatus>? StatusReceived;
    public event Action<string>? BannerReceived;   // READY
    public event Action? Disconnected;
    public event Action<LinePayload>? UnsolicitedResponse;   // 未配对的应答

    /// <summary>
    /// 发送一条指令并等待应答。timeout 默认 2 s（docs/20 §9.1）。
    /// </summary>
    public async Task<RobotResponse> SendAsync(string command, TimeSpan? timeout = null, CancellationToken ct = default)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (!_transport.IsConnected)
            return RobotResponse.Local(LocalError.NotConnected, "未连接");

        var effectiveTimeout = timeout ?? TimeSpan.FromSeconds(2);
        await _sendLock.WaitAsync(ct);
        try
        {
            var tcs = new TaskCompletionSource<RobotResponse>(TaskCreationOptions.RunContinuationsAsynchronously);
            _pending = tcs;
            _discardNextResponse = false;

            RawLine?.Invoke(command, true);
            _transport.SendLine(command);

            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            var completed = await Task.WhenAny(tcs.Task, Task.Delay(effectiveTimeout, cts.Token));
            cts.Cancel();

            if (completed == tcs.Task)
            {
                var resp = await tcs.Task;
                RawLine?.Invoke(resp.RawLine, false);
                if (resp.Payload is OkLine && resp.Data is not null && RobotStatus.TryParse(resp.Data, out var st))
                    StatusReceived?.Invoke(st!);
                return resp;
            }

            // 超时：丢弃后续迟到应答
            _pending = null;
            _discardNextResponse = true;
            var timeoutResp = RobotResponse.Local(LocalError.Timeout, $"应答超时（{effectiveTimeout.TotalSeconds:0.#}s）");
            RawLine?.Invoke($"# {timeoutResp.ErrorMessage}", false);
            return timeoutResp;
        }
        finally
        {
            _pending = null;
            _sendLock.Release();
        }
    }

    private void OnLineReceived(string line)
    {
        var payload = LineParser.Parse(line);
        if (payload is null) return;

        switch (payload)
        {
            case EvtLine evt:
                RawLine?.Invoke(line, false);
                if (evt.Kind == "STATUS" && RobotStatus.TryParse(evt.Rest, out var st))
                    StatusReceived?.Invoke(st!);
                EventReceived?.Invoke(evt);
                return;

            case ReadyLine ready:
                RawLine?.Invoke(line, false);
                BannerReceived?.Invoke(ready.Firmware);
                return;

            case PongLine or OkLine or ErrLine:
                if (_discardNextResponse)
                {
                    _discardNextResponse = false;
                    RawLine?.Invoke($"# 丢弃迟到应答: {line}", false);
                    return;
                }
                var pending = _pending;
                if (pending is not null)
                {
                    pending.TrySetResult(RobotResponse.FromPayload(payload, line));
                }
                else
                {
                    RawLine?.Invoke(line, false);
                    UnsolicitedResponse?.Invoke(payload);
                }
                return;

            default:
                RawLine?.Invoke(line, false);
                UnsolicitedResponse?.Invoke(payload);
                return;
        }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _transport.LineReceived -= OnLineReceived;
        _sendLock.Dispose();
    }
}

/// <summary>一条指令的最终应答。</summary>
public sealed record RobotResponse(
    bool Success,
    string? Data,
    RobotErrorCode ErrorCode,
    LocalError LocalError,
    string? ErrorMessage,
    string RawLine,
    LinePayload Payload)
{
    public static RobotResponse FromPayload(LinePayload payload, string raw) => payload switch
    {
        OkLine ok => new RobotResponse(true, ok.Data, RobotErrorCode.OK, LocalError.None, null, raw, payload),
        PongLine => new RobotResponse(true, null, RobotErrorCode.OK, LocalError.None, null, raw, payload),
        ErrLine err => new RobotResponse(false, null, err.Code, LocalError.None, err.Message, raw, payload),
        _ => new RobotResponse(false, null, RobotErrorCode.OK, LocalError.ParseError, "无法识别的应答", raw, payload),
    };

    public static RobotResponse Local(LocalError err, string message) =>
        new(false, null, RobotErrorCode.OK, err, message, string.Empty,
            new ErrLine(RobotErrorCode.OK, message));
}
