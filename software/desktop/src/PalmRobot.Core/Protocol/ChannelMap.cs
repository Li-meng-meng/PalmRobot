namespace PalmRobot.Core.Protocol;

/// <summary>
/// 8 通道关节映射与预设姿态（docs/20 §5.2、docs/21 §5，冻结基准）。
/// ch 序：R1,R2,L1,L2,R4,R3,L3,L4。
/// </summary>
public static class ChannelMap
{
    public static readonly string[] JointNames = { "R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4" };
    public static readonly string[] JointParts = { "前右髋", "后右髋", "前左髋", "后左髋", "后右膝", "前右膝", "前左膝", "后左膝" };

    /// <summary>机械限位（°），下标 = 通道号。</summary>
    public static readonly (int Min, int Max)[] Limits =
    {
        (45, 180), (0, 135), (0, 135), (45, 180), (0, 180), (0, 180), (0, 180), (0, 180),
    };

    public static readonly int[] StandPose = { 135, 45, 45, 135, 0, 180, 0, 180 };
    public static readonly int[] SitPose = { 135, 75, 45, 75, 50, 170, 20, 170 };
    public static readonly int[] SleepPose = { 135, 80, 45, 80, 60, 160, 20, 160 };

    public const int MinAngle = 0;
    public const int MaxAngle = 180;
    public const int MinFrameMs = 20;
    public const int MaxFrameT = 600000;
    public const string PoseStand = "stand";
    public const string PoseSit = "sit";
    public const string PoseWave = "wave";
    public const string PoseDance = "dance";
    public const string PoseSleep = "sleep";
}
