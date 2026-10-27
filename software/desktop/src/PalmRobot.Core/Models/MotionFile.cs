using System.Text.Json;
using System.Text.Json.Serialization;

namespace PalmRobot.Core.Models;

public sealed class MotionFile
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("version")]
    public int Version { get; set; } = 1;

    [JsonPropertyName("duration_ms")]
    public int DurationMs { get; set; }

    [JsonPropertyName("loop")]
    public bool Loop { get; set; }

    [JsonPropertyName("tracks")]
    public MotionTracks Tracks { get; set; } = new();

    [JsonPropertyName("meta")]
    public MotionMeta? Meta { get; set; }

    public static MotionFile FromJson(string json, JsonSerializerOptions? options = null)
        => JsonSerializer.Deserialize<MotionFile>(json, options ?? MotionJsonOptions.Default)
           ?? throw new JsonException("顶层不是有效对象");
}

public sealed class MotionTracks
{
    [JsonPropertyName("servo")]
    public List<ServoFrame> Servo { get; set; } = new();

    [JsonPropertyName("face")]
    public List<FaceFrame> Face { get; set; } = new();

    [JsonPropertyName("audio")]
    public List<AudioFrame> Audio { get; set; } = new();
}

public sealed class ServoFrame
{
    [JsonPropertyName("t")]
    public int T { get; set; }

    [JsonPropertyName("ch")]
    [JsonConverter(typeof(IntArrayConverter))]
    public int[] Ch { get; set; } = new int[8];
}

public sealed class FaceFrame
{
    [JsonPropertyName("t")]
    public int T { get; set; }

    [JsonPropertyName("expr")]
    [JsonConverter(typeof(IntArrayConverter))]
    public int[] Expr { get; set; } = new int[12];
}

public sealed class AudioFrame
{
    [JsonPropertyName("t")]
    public int T { get; set; }

    [JsonPropertyName("clip")]
    public string Clip { get; set; } = string.Empty;
}

public sealed class MotionMeta
{
    [JsonPropertyName("author")]
    public string? Author { get; set; }

    [JsonPropertyName("source")]
    public string? Source { get; set; }

    [JsonPropertyName("tags")]
    public List<string>? Tags { get; set; }

    [JsonPropertyName("created")]
    public string? Created { get; set; }
}

public static class MotionJsonOptions
{
    public static readonly JsonSerializerOptions Default = new()
    {
        PropertyNamingPolicy = null,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
}

/// <summary>容错数组转换：接受 int[] 和 JsonElement[]。</summary>
internal sealed class IntArrayConverter : JsonConverter<int[]>
{
    public override int[] Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType != JsonTokenType.StartArray)
            throw new JsonException("期望数组");
        var list = new List<int>();
        while (reader.Read() && reader.TokenType != JsonTokenType.EndArray)
        {
            if (reader.TokenType == JsonTokenType.Number)
                list.Add(reader.TryGetInt32(out var v) ? v : (int)reader.GetDouble());
            else
                throw new JsonException("数组元素必须是数字");
        }
        return list.ToArray();
    }

    public override void Write(Utf8JsonWriter writer, int[] value, JsonSerializerOptions options)
    {
        writer.WriteStartArray();
        foreach (var v in value) writer.WriteNumberValue(v);
        writer.WriteEndArray();
    }
}
