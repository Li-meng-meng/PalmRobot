using System.Text;

namespace PalmRobot.Core.Protocol;

/// <summary>指令构造器（docs/20 §5 指令表）。只拼字符串，不做网络 IO。</summary>
public static class RobotCommand
{
    // —— 运动控制 ——
    public static string Move(int v, int w)
    {
        ValidateRange(v, -100, 100, nameof(v));
        ValidateRange(w, -100, 100, nameof(w));
        return $"MOVE {v} {w}";
    }

    public static string Stop() => "STOP";

    public static string Pose(string name)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(name);
        return $"POSE {name}";
    }

    public static string Play(string motionId)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(motionId);
        return $"PLAY {motionId}";
    }

    // —— 动作编辑 ——
    public static string Frame(int t, ReadOnlySpan<int> ch)
    {
        ValidateRange(t, 0, ChannelMap.MaxFrameT, nameof(t));
        if (ch.Length != 8) throw new ArgumentException("需要恰好 8 个通道角度", nameof(ch));
        var sb = new StringBuilder("FRAME ").Append(t);
        foreach (var a in ch)
        {
            ValidateRange(a, ChannelMap.MinAngle, ChannelMap.MaxAngle, nameof(ch));
            sb.Append(' ').Append(a);
        }
        return sb.ToString();
    }

    public static string Frames(int n)
    {
        ValidateRange(n, 0, 100000, nameof(n));
        return $"FRAMES {n}";
    }

    // —— 表情 ——
    public static string Face(ReadOnlySpan<int> expr)
    {
        if (expr.Length != 12) throw new ArgumentException("需要恰好 12 个表情参数", nameof(expr));
        var sb = new StringBuilder("FACE");
        for (var i = 0; i < expr.Length; i++)
        {
            var range = i is 8 or 9 ? (-100, 100) : (0, 100);
            ValidateRange(expr[i], range.Item1, range.Item2, $"{nameof(expr)}[{i}]");
            sb.Append(' ').Append(expr[i]);
        }
        return sb.ToString();
    }

    public static string FacePreset(string preset)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(preset);
        return $"FACE {preset}";
    }

    // —— 状态与心跳 ——
    public static string Status() => "STATUS";
    public static string Ping() => "PING";
    public static string Ver() => "VER";

    // —— 配置 ——
    public static string CfgGet() => "CFG GET";
    public static string CfgGet(string key)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(key);
        return $"CFG GET {key}";
    }
    public static string CfgSet(string key, string value)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(key);
        return $"CFG SET {key} {value}";
    }

    // —— 维护与标定 ——
    public static string Cal(int ch, int angle)
    {
        ValidateRange(ch, 0, 7, nameof(ch));
        ValidateRange(angle, 0, 180, nameof(angle));
        return $"CAL {ch} {angle}";
    }
    public static string CalSave() => "CAL SAVE";
    public static string CalGet() => "CAL GET";
    public static string Wifi(string ssid, string pwd) => $"WIFI {ssid} {pwd}";
    public static string Save() => "SAVE";
    public static string Reboot() => "REBOOT";
    public static string Reset() => "RESET";

    // —— 动作包管理 ——
    public static string Putm(string name, int sizeBytes)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(name);
        ValidateRange(sizeBytes, 1, 16384, nameof(sizeBytes));
        return $"PUTM {name} {sizeBytes}";
    }
    public static string Listm() => "LISTM";
    public static string Delm(string name)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(name);
        return $"DELM {name}";
    }

    private static void ValidateRange(int value, int min, int max, string name)
    {
        if (value < min || value > max)
            throw new ArgumentOutOfRangeException(name, value, $"允许范围 {min}..{max}");
    }
}
