using System.Text.Json;

namespace PalmRobot.Core.Protocol;

/// <summary>状态 JSON（docs/20 §4 冻结 5 字段，未知字段忽略）。</summary>
public sealed record RobotStatus(
    double Voltage,
    int TemperatureC,
    int BatteryPercent,
    string Pose,
    RobotErrorCode LastError)
{
    private static readonly JsonSerializerOptions Options = new() { PropertyNameCaseInsensitive = false };

    public static bool TryParse(string json, out RobotStatus? status)
    {
        status = null;
        try
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return false;

            double v = 0; int t = 0, batt = 0, err = 0;
            string pose = "unknown";

            if (root.TryGetProperty("v", out var ve) && ve.ValueKind == JsonValueKind.Number) v = ve.GetDouble();
            if (root.TryGetProperty("t", out var te) && te.ValueKind == JsonValueKind.Number) t = te.GetInt32();
            if (root.TryGetProperty("batt", out var be) && be.ValueKind == JsonValueKind.Number) batt = be.GetInt32();
            if (root.TryGetProperty("pose", out var pe) && pe.ValueKind == JsonValueKind.String) pose = pe.GetString() ?? "unknown";
            if (root.TryGetProperty("err", out var ee) && ee.ValueKind == JsonValueKind.Number)
            {
                err = ee.GetInt32();
                if (!Enum.IsDefined(typeof(RobotErrorCode), err)) err = (int)RobotErrorCode.UnknownCmd;
            }

            status = new RobotStatus(v, t, batt, pose, (RobotErrorCode)err);
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
    }
}
