namespace PalmRobot.Core.Protocol;

/// <summary>错误码（docs/20 §8，冻结，只增不改）。</summary>
public enum RobotErrorCode
{
    OK = 0,
    BadArgs = 1,
    UnknownCmd = 2,
    Busy = 3,
    PowerFault = 4,
    BattLow = 5,
    Stall = 6,
    StorageFull = 7,
    NotFound = 8,
    Oversize = 9,
    Timeout = 10,
    Checksum = 11,
    CalLock = 12,
}

/// <summary>本机侧产生的错误（非固件返回，不占用固件错误码段）。</summary>
public enum LocalError
{
    None = 0,
    Timeout = -1,
    NotConnected = -2,
    ParseError = -3,
}
