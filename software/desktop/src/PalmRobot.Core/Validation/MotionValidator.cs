using System.Text.Json;
using System.Text.RegularExpressions;
using PalmRobot.Core.Models;
using PalmRobot.Core.Protocol;

namespace PalmRobot.Core.Validation;

/// <summary>校验错误（规则编号对齐 docs/21 §4）。</summary>
public sealed record ValidationError(int Rule, string Message);

public sealed record MotionValidationResult(bool IsValid, IReadOnlyList<ValidationError> Errors)
{
    public static readonly MotionValidationResult Ok = new(true, Array.Empty<ValidationError>());
}

/// <summary>
/// .motion v1 校验（docs/21 §4 九条规则，与固件共同实现，与 Python motion_gen.py 一致）。
/// </summary>
public static partial class MotionValidator
{
    private const int MaxFileBytes = 16384;
    private const int MinDurationMs = 100;
    private const int MaxDurationMs = 600000;
    private const int MinFrameGapMs = ChannelMap.MinFrameMs;   // 20

    [GeneratedRegex("^[a-z0-9_-]{1,32}$")]
    private static partial Regex NameRegex();

    [GeneratedRegex("^[a-z0-9_-]{1,32}$")]
    private static partial Regex ClipRegex();

    public static MotionValidationResult Validate(MotionFile motion, int? fileByteCount = null)
    {
        var errors = new List<ValidationError>();

        // R1: version == 1（JSON 可解析由反序列化保证）
        if (motion.Version != 1)
            errors.Add(new(1, $"version 必须为 1，当前 {motion.Version}"));

        // R2: 必填字段与类型
        if (string.IsNullOrEmpty(motion.Name) || !NameRegex().IsMatch(motion.Name))
            errors.Add(new(2, "name 必须是 1-32 字符且只含 [a-z0-9_-]"));
        if (motion.DurationMs < MinDurationMs || motion.DurationMs > MaxDurationMs)
            errors.Add(new(2, $"duration_ms 必须在 {MinDurationMs}-{MaxDurationMs}，当前 {motion.DurationMs}"));
        if (motion.Tracks.Servo.Count == 0)
            errors.Add(new(2, "tracks.servo 缺失或为空"));

        // R3/R4/R6/R7/R8: servo 轨道
        var prevT = -1;
        for (var i = 0; i < motion.Tracks.Servo.Count; i++)
        {
            var f = motion.Tracks.Servo[i];
            if (f.T < 0) errors.Add(new(3, $"servo[{i}].t 为负数"));
            if (f.T < prevT) errors.Add(new(3, $"servo[{i}].t 非降序（{f.T} < {prevT}）"));
            prevT = f.T;
            if (f.Ch.Length != 8) errors.Add(new(4, $"servo[{i}].ch 长度必须为 8，当前 {f.Ch.Length}"));
            for (var c = 0; c < f.Ch.Length && c < 8; c++)
            {
                if (f.Ch[c] < ChannelMap.MinAngle || f.Ch[c] > ChannelMap.MaxAngle)
                    errors.Add(new(4, $"servo[{i}].ch[{c}]={f.Ch[c]} 超出 0-180"));
            }
            if (f.T > motion.DurationMs)
                errors.Add(new(8, $"servo[{i}].t={f.T} 超出 duration_ms={motion.DurationMs}"));
            if (i > 0 && f.T - motion.Tracks.Servo[i - 1].T < MinFrameGapMs)
                errors.Add(new(7, $"servo[{i}] 与前一帧间隔 {f.T - motion.Tracks.Servo[i - 1].T}ms < {MinFrameGapMs}ms"));
        }
        if (motion.Tracks.Servo.Count > 0 && motion.Tracks.Servo[0].T != 0)
            errors.Add(new(6, "servo 首帧 t 必须为 0"));

        // R5: face 轨道
        prevT = -1;
        for (var i = 0; i < motion.Tracks.Face.Count; i++)
        {
            var f = motion.Tracks.Face[i];
            if (f.T < 0) errors.Add(new(3, $"face[{i}].t 为负数"));
            if (f.T < prevT) errors.Add(new(3, $"face[{i}].t 非降序（{f.T} < {prevT}）"));
            prevT = f.T;
            if (f.Expr.Length != 12) errors.Add(new(5, $"face[{i}].expr 长度必须为 12，当前 {f.Expr.Length}"));
            for (var c = 0; c < f.Expr.Length && c < 12; c++)
            {
                var min = c is 8 or 9 ? -100 : 0;
                if (f.Expr[c] < min || f.Expr[c] > 100)
                    errors.Add(new(5, $"face[{i}].expr[{c}]={f.Expr[c]} 超出 {min}-100"));
            }
            if (f.T > motion.DurationMs)
                errors.Add(new(8, $"face[{i}].t={f.T} 超出 duration_ms={motion.DurationMs}"));
        }

        // audio 轨道
        prevT = -1;
        for (var i = 0; i < motion.Tracks.Audio.Count; i++)
        {
            var f = motion.Tracks.Audio[i];
            if (f.T < 0) errors.Add(new(3, $"audio[{i}].t 为负数"));
            if (f.T < prevT) errors.Add(new(3, $"audio[{i}].t 非降序（{f.T} < {prevT}）"));
            prevT = f.T;
            if (string.IsNullOrEmpty(f.Clip) || !ClipRegex().IsMatch(f.Clip))
                errors.Add(new(5, $"audio[{i}].clip 必须是 1-32 字符且只含 [a-z0-9_-]"));
            if (f.T > motion.DurationMs)
                errors.Add(new(8, $"audio[{i}].t={f.T} 超出 duration_ms={motion.DurationMs}"));
        }

        // meta.tags
        if (motion.Meta?.Tags is { Count: > 8 } tags)
            errors.Add(new(2, $"meta.tags 最多 8 个，当前 {tags.Count}"));

        // R9: 文件大小
        if (fileByteCount is { } size && size > MaxFileBytes)
            errors.Add(new(9, $"文件大小 {size} 字节超过 {MaxFileBytes} 上限"));

        return errors.Count == 0 ? MotionValidationResult.Ok : new MotionValidationResult(false, errors);
    }

    /// <summary>从 JSON 文本校验（含可解析性检查）。</summary>
    public static MotionValidationResult ValidateJson(string json, int? fileByteCount = null)
    {
        MotionFile? motion;
        try
        {
            motion = MotionFile.FromJson(json);
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException)
        {
            return new MotionValidationResult(false, new[] { new ValidationError(1, $"JSON 无法解析或顶层不是对象: {ex.Message}") });
        }
        return Validate(motion, fileByteCount);
    }
}
