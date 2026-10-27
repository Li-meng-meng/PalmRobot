using PalmRobot.Core.Protocol;
using Xunit;

namespace PalmRobot.Core.Tests;

public class LineParserTests
{
    [Fact]
    public void Parse_OkWithoutData()
    {
        var p = LineParser.Parse("OK\r");
        var ok = Assert.IsType<OkLine>(p);
        Assert.Null(ok.Data);
    }

    [Fact]
    public void Parse_OkWithData_PreservesRest()
    {
        var p = LineParser.Parse("OK {\"v\":12.4,\"t\":45}");
        var ok = Assert.IsType<OkLine>(p);
        Assert.Equal("{\"v\":12.4,\"t\":45}", ok.Data);
    }

    [Fact]
    public void Parse_Err()
    {
        var p = Assert.IsType<ErrLine>(LineParser.Parse("ERR 1 BAD_RANGE"));
        Assert.Equal(RobotErrorCode.BadArgs, p.Code);
        Assert.Equal("BAD_RANGE", p.Message);
    }

    [Fact]
    public void Parse_ErrBadCode_FallsBack()
    {
        var p = Assert.IsType<ErrLine>(LineParser.Parse("ERR 99 WEIRD"));
        Assert.Equal(RobotErrorCode.UnknownCmd, p.Code);
    }

    [Fact]
    public void Parse_Pong()
    {
        Assert.IsType<PongLine>(LineParser.Parse("PONG"));
    }

    [Fact]
    public void Parse_Ready()
    {
        var p = Assert.IsType<ReadyLine>(LineParser.Parse("READY v0.3.1"));
        Assert.Equal("v0.3.1", p.Firmware);
    }

    [Fact]
    public void Parse_Evt()
    {
        var p = Assert.IsType<EvtLine>(LineParser.Parse("EVT STATUS {\"v\":12.4,\"t\":45,\"batt\":80,\"pose\":\"stand\",\"err\":0}"));
        Assert.Equal("STATUS", p.Kind);
        Assert.Contains("\"v\":12.4", p.Rest);

        var play = Assert.IsType<EvtLine>(LineParser.Parse("EVT PLAY wave DONE"));
        Assert.Equal("PLAY", play.Kind);
        Assert.Equal("wave DONE", play.Rest);

        var wifi = Assert.IsType<EvtLine>(LineParser.Parse("EVT WIFI FAIL"));
        Assert.Equal("WIFI", wifi.Kind);
        Assert.Equal("FAIL", wifi.Rest);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Parse_Empty_ReturnsNull(string? line)
    {
        Assert.Null(LineParser.Parse(line));
    }

    [Fact]
    public void Parse_UnknownLine()
    {
        Assert.IsType<UnknownLine>(LineParser.Parse("HELLO WORLD"));
    }
}

public class RobotStatusTests
{
    [Fact]
    public void TryParse_FrozenFields()
    {
        var ok = RobotStatus.TryParse("{\"v\":12.4,\"t\":45,\"batt\":80,\"pose\":\"stand\",\"err\":0}", out var s);
        Assert.True(ok);
        Assert.Equal(12.4, s!.Voltage);
        Assert.Equal(45, s.TemperatureC);
        Assert.Equal(80, s.BatteryPercent);
        Assert.Equal("stand", s.Pose);
        Assert.Equal(RobotErrorCode.OK, s.LastError);
    }

    [Fact]
    public void TryParse_IgnoresUnknownFields()
    {
        var ok = RobotStatus.TryParse("{\"v\":11.9,\"t\":50,\"batt\":64,\"pose\":\"motion\",\"err\":6,\"rssi\":-52,\"uptime\":123}", out var s);
        Assert.True(ok);
        Assert.Equal(RobotErrorCode.Stall, s!.LastError);
        Assert.Equal("motion", s.Pose);
    }

    [Theory]
    [InlineData("not json", false)]
    [InlineData("[1,2,3]", false)]
    [InlineData("{}", true)]
    public void TryParse_BadInput(string json, bool expected)
    {
        var ok = RobotStatus.TryParse(json, out _);
        Assert.Equal(expected, ok);
    }
}

public class RobotCommandTests
{
    [Fact]
    public void Frame_BuildsFullLine()
    {
        var cmd = RobotCommand.Frame(1200, new[] { 135, 45, 45, 135, 0, 180, 0, 180 });
        Assert.Equal("FRAME 1200 135 45 45 135 0 180 0 180", cmd);
    }

    [Theory]
    [InlineData(181)]
    [InlineData(-1)]
    public void Frame_AngleOutOfRange_Throws(int bad)
    {
        var ch = new[] { 90, 90, 90, 90, 90, 90, 90, bad };
        Assert.Throws<ArgumentOutOfRangeException>(() => RobotCommand.Frame(0, ch));
    }

    [Fact]
    public void Move_OutOfRange_Throws()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => RobotCommand.Move(101, 0));
        Assert.Throws<ArgumentOutOfRangeException>(() => RobotCommand.Move(0, -101));
    }

    [Fact]
    public void Face_GazeAllowsNegative()
    {
        var expr = new[] { 80, 10, 60, 40, 30, 90, 60, 50, -100, 100, 40, 80 };
        Assert.Equal("FACE 80 10 60 40 30 90 60 50 -100 100 40 80", RobotCommand.Face(expr));
    }

    [Fact]
    public void Face_NonGazeNegative_Throws()
    {
        var expr = new[] { 80, 10, 60, 40, 30, 90, 60, 50, 0, 0, -1, 80 };
        Assert.Throws<ArgumentOutOfRangeException>(() => RobotCommand.Face(expr));
    }
}

public class ChannelMapTests
{
    [Fact]
    public void StandPose_MatchesSpec()
    {
        Assert.Equal(new[] { 135, 45, 45, 135, 0, 180, 0, 180 }, ChannelMap.StandPose);
    }

    [Fact]
    public void JointNames_MatchChannelOrder()
    {
        Assert.Equal(new[] { "R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4" }, ChannelMap.JointNames);
    }
}
