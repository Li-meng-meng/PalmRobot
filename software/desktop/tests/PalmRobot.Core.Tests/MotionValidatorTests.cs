using PalmRobot.Core.Models;
using PalmRobot.Core.Validation;
using Xunit;

namespace PalmRobot.Core.Tests;

public class MotionValidatorTests
{
    private const string ValidJson = """
        {"name":"wave","version":1,"duration_ms":2000,"loop":false,
         "tracks":{"servo":[{"t":0,"ch":[90,90,90,90,90,90,90,90]},{"t":400,"ch":[90,60,90,120,90,90,90,90]}],
                   "face":[{"t":0,"expr":[80,10,60,40,30,90,60,50,0,0,40,80]}]},
         "meta":{"author":"software","source":"manual","tags":["greeting"],"created":"2026-09-17"}}
        """;

    [Fact]
    public void ValidFile_Passes()
    {
        var result = MotionValidator.ValidateJson(ValidJson);
        Assert.True(result.IsValid, string.Join("; ", result.Errors.Select(e => e.Message)));
    }

    [Fact]
    public void BadJson_FailsRule1()
    {
        var result = MotionValidator.ValidateJson("{broken");
        Assert.False(result.IsValid);
        Assert.Equal(1, result.Errors[0].Rule);
    }

    [Fact]
    public void WrongVersion_FailsRule1()
    {
        var json = """{"name":"x","version":2,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":0,"ch":[90,90,90,90,90,90,90,90]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 1);
    }

    [Theory]
    [InlineData("Wave")]       // 大写
    [InlineData("has space")]
    [InlineData("")]
    [InlineData("this_name_is_way_too_long_over_32ch")]
    public void BadName_FailsRule2(string name)
    {
        var safeName = name.Replace("\"", "\\\"");
        var json = "{\"name\":\"" + safeName + "\",\"version\":1,\"duration_ms\":1000,\"loop\":false,\"tracks\":{\"servo\":[{\"t\":0,\"ch\":[90,90,90,90,90,90,90,90]}]}}";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 2);
    }

    [Fact]
    public void DescendingT_FailsRule3()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":400,"ch":[90,90,90,90,90,90,90,90]},{"t":200,"ch":[90,90,90,90,90,90,90,90]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 3);
    }

    [Fact]
    public void ChLengthNot8_FailsRule4()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":0,"ch":[90,90,90]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 4);
    }

    [Fact]
    public void AngleOutOfRange_FailsRule4()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":0,"ch":[90,90,90,90,90,90,90,181]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 4);
    }

    [Fact]
    public void ExprWrongLength_FailsRule5()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":0,"ch":[90,90,90,90,90,90,90,90]}],"face":[{"t":0,"expr":[80,10]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 5);
    }

    [Fact]
    public void GazeAllowsNegative()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":0,"ch":[90,90,90,90,90,90,90,90]}],"face":[{"t":0,"expr":[80,10,60,40,30,90,60,50,-100,100,40,80]}]}}""";
        Assert.True(MotionValidator.ValidateJson(json).IsValid);
    }

    [Fact]
    public void FirstFrameNotZero_FailsRule6()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":20,"ch":[90,90,90,90,90,90,90,90]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 6);
    }

    [Fact]
    public void GapTooSmall_FailsRule7()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":0,"ch":[90,90,90,90,90,90,90,90]},{"t":19,"ch":[90,90,90,90,90,90,90,90]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 7);
    }

    [Fact]
    public void TExceedsDuration_FailsRule8()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":0,"ch":[90,90,90,90,90,90,90,90]},{"t":1001,"ch":[90,90,90,90,90,90,90,90]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 8);
    }

    [Fact]
    public void OversizeFile_FailsRule9()
    {
        var motion = MotionFile.FromJson(ValidJson);
        var result = MotionValidator.Validate(motion, 16385);
        Assert.Contains(result.Errors, e => e.Rule == 9);
    }

    [Fact]
    public void MissingServo_FailsRule2()
    {
        var json = """{"name":"x","version":1,"duration_ms":1000,"loop":false,"tracks":{"face":[{"t":0,"expr":[80,10,60,40,30,90,60,50,0,0,40,80]}]}}""";
        var result = MotionValidator.ValidateJson(json);
        Assert.Contains(result.Errors, e => e.Rule == 2);
    }

    [Fact]
    public void SampleMotions_AllPass()
    {
        // 样例动作包目录（T1 产出），作为共同测试资产
        var dir = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "tools", "motions");
        if (!Directory.Exists(dir)) return;   // 环境缺失时跳过

        foreach (var file in Directory.GetFiles(dir, "*.motion"))
        {
            var json = File.ReadAllText(file);
            var result = MotionValidator.ValidateJson(json, (int)new FileInfo(file).Length);
            Assert.True(result.IsValid, $"{Path.GetFileName(file)}: {string.Join("; ", result.Errors.Select(e => e.Message))}");
        }
    }
}
