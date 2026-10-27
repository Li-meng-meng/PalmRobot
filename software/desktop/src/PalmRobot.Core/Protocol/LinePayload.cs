namespace PalmRobot.Core.Protocol;

/// <summary>一行协议负载（docs/20 §3）。</summary>
public abstract record LinePayload;

/// <summary>OK / OK &lt;data&gt;</summary>
public sealed record OkLine(string? Data) : LinePayload;

/// <summary>ERR &lt;code&gt; &lt;msg&gt;</summary>
public sealed record ErrLine(RobotErrorCode Code, string Message) : LinePayload;

/// <summary>PONG（PING 应答，无 OK 前缀）</summary>
public sealed record PongLine : LinePayload;

/// <summary>READY &lt;fw_version&gt;（上电横幅）</summary>
public sealed record ReadyLine(string Firmware) : LinePayload;

/// <summary>EVT &lt;kind&gt; &lt;rest&gt;（服务器主动推送，docs/20 §7）</summary>
public sealed record EvtLine(string Kind, string Rest) : LinePayload;

/// <summary>无法识别的行。</summary>
public sealed record UnknownLine(string Raw) : LinePayload;

public static class LineParser
{
    /// <summary>解析一行（忽略行尾 \r；空行返回 null）。</summary>
    public static LinePayload? Parse(string? line)
    {
        if (line is null) return null;
        line = line.TrimEnd('\r', '\n');
        if (line.Length == 0) return null;

        line = line.Trim();
        if (line.Length == 0) return null;

        var firstSpace = line.IndexOf(' ');
        var first = firstSpace < 0 ? line : line[..firstSpace];
        var rest = firstSpace < 0 ? string.Empty : line[(firstSpace + 1)..].Trim();

        switch (first.ToUpperInvariant())
        {
            case "OK":
                return rest.Length == 0 ? new OkLine(null) : new OkLine(rest);
            case "ERR":
            {
                var restTokens = rest.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (restTokens.Length >= 1 && int.TryParse(restTokens[0], out var code)
                    && Enum.IsDefined(typeof(RobotErrorCode), code))
                {
                    var msg = restTokens.Length >= 2 ? restTokens[1] : string.Empty;
                    return new ErrLine((RobotErrorCode)code, msg);
                }
                return new ErrLine(RobotErrorCode.UnknownCmd, line);
            }
            case "PONG":
                return new PongLine();
            case "READY":
                return new ReadyLine(rest);
            case "EVT":
            {
                var firstSpaceInRest = rest.IndexOf(' ');
                if (firstSpaceInRest < 0) return new UnknownLine(line);
                return new EvtLine(rest[..firstSpaceInRest], rest[(firstSpaceInRest + 1)..]);
            }
            default:
                return new UnknownLine(line);
        }
    }
}
