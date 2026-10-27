using PalmRobot.Core;
using PalmRobot.Core.Protocol;
using PalmRobot.Core.Transport;
using Xunit;

namespace PalmRobot.Core.Tests;

public class RobotClientTests
{
    private static async Task<(RobotClient Client, MockRobotTransport Transport)> CreateConnectedAsync()
    {
        var transport = new MockRobotTransport();
        var client = new RobotClient(transport);
        await transport.ConnectAsync();
        return (client, transport);
    }

    [Fact]
    public async Task Ping_Pongs()
    {
        var (client, _) = await CreateConnectedAsync();
        var resp = await client.SendAsync(RobotCommand.Ping());
        Assert.True(resp.Success);
        Assert.IsType<PongLine>(resp.Payload);
    }

    [Fact]
    public async Task Status_ReturnsParseableJson()
    {
        var (client, _) = await CreateConnectedAsync();
        RobotStatus? received = null;
        client.StatusReceived += s => received = s;

        var resp = await client.SendAsync(RobotCommand.Status());
        Assert.True(resp.Success);
        Assert.NotNull(resp.Data);
        Assert.True(RobotStatus.TryParse(resp.Data!, out var parsed));
        Assert.Equal("stand", parsed!.Pose);
        Assert.NotNull(received);
    }

    [Fact]
    public async Task Frame_UpdatesMock()
    {
        var (client, _) = await CreateConnectedAsync();
        var resp = await client.SendAsync(RobotCommand.Frame(0, new[] { 100, 80, 80, 100, 10, 170, 10, 170 }));
        Assert.True(resp.Success);
    }

    [Fact]
    public async Task UnknownCmd_ReturnsErr2()
    {
        var (client, _) = await CreateConnectedAsync();
        var resp = await client.SendAsync("FLY TO MOON");
        Assert.False(resp.Success);
        Assert.Equal(RobotErrorCode.UnknownCmd, resp.ErrorCode);
    }

    [Fact]
    public async Task CfgGet_ReturnsJson()
    {
        var (client, _) = await CreateConnectedAsync();
        var resp = await client.SendAsync(RobotCommand.CfgGet());
        Assert.True(resp.Success);
        Assert.Contains("gait.stride", resp.Data);
    }

    [Fact]
    public async Task Putm_FullUpload()
    {
        var (client, _) = await CreateConnectedAsync();
        var json = """{"name":"test1","version":1,"duration_ms":1000,"loop":false,"tracks":{"servo":[{"t":0,"ch":[90,90,90,90,90,90,90,90]},{"t":500,"ch":[90,60,90,120,90,90,90,90]}]}}""";

        var declare = await client.SendAsync(RobotCommand.Putm("test1", json.Length));
        Assert.True(declare.Success);
        Assert.Equal("PUT_READY", declare.Data);

        var data = await client.SendAsync(json);
        Assert.True(data.Success);
        Assert.Equal("SAVED", data.Data);

        var list = await client.SendAsync(RobotCommand.Listm());
        Assert.True(list.Success);
        Assert.Contains("test1", list.Data);
    }

    [Fact]
    public async Task Evt_DoesNotBreakPairing()
    {
        var (client, transport) = await CreateConnectedAsync();
        var events = new List<EvtLine>();
        client.EventReceived += e => events.Add(e);

        // WIFI 触发异步 EVT，应答配对不应被 EVT 干扰
        await client.SendAsync(RobotCommand.Wifi("ssid", "pwd"));
        await client.SendAsync(RobotCommand.Ping());
        await Task.Delay(600);   // 等 EVT WIFI

        Assert.Contains(events, e => e.Kind == "WIFI");
    }

    [Fact]
    public async Task Timeout_WhenNoResponse()
    {
        var transport = new SilentTransport();
        var client = new RobotClient(transport);
        await transport.ConnectAsync();

        var sw = System.Diagnostics.Stopwatch.StartNew();
        var resp = await client.SendAsync("PING", TimeSpan.FromMilliseconds(200));
        sw.Stop();

        Assert.False(resp.Success);
        Assert.Equal(LocalError.Timeout, resp.LocalError);
        Assert.True(sw.ElapsedMilliseconds >= 180);
    }

    [Fact]
    public async Task StrictPairing_SequentialSends()
    {
        var (client, _) = await CreateConnectedAsync();
        var tasks = new List<Task<RobotResponse>>();
        for (var i = 0; i < 5; i++)
        {
            tasks.Add(client.SendAsync(RobotCommand.Ping()));
        }
        var responses = await Task.WhenAll(tasks);
        Assert.All(responses, r => Assert.True(r.Success));
    }

    [Fact]
    public async Task BatchFrames_LastFrameOkDone()
    {
        var (client, _) = await CreateConnectedAsync();
        Assert.True((await client.SendAsync(RobotCommand.Frames(2))).Success);
        Assert.True((await client.SendAsync(RobotCommand.Frame(0, new[] { 135, 45, 45, 135, 0, 180, 0, 180 }))).Success);
        var last = await client.SendAsync(RobotCommand.Frame(100, new[] { 135, 45, 45, 135, 0, 180, 0, 180 }));
        Assert.True(last.Success);
        Assert.Equal("DONE", last.Data);
    }

    /// <summary>收到指令不回应答的假设备（超时测试用）。</summary>
    private sealed class SilentTransport : ITransport
    {
        public bool IsConnected { get; private set; }
        public string Description => "Silent";
        public event Action<string>? LineReceived;
        public event Action? Disconnected;
        public Task ConnectAsync(CancellationToken ct = default) { IsConnected = true; return Task.CompletedTask; }
        public void Disconnect() { }
        public void SendLine(string line) { /* 静默 */ }
        public void Dispose() { }
    }
}
