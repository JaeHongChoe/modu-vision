// .NET 8 console application: replace Program.cs with this file.
// dotnet run -- ./image.png http://127.0.0.1:8765
using System.Net.Http;
using System.Text.Json;

if (args.Length < 1) throw new ArgumentException("Image path is required");
var token = Environment.GetEnvironmentVariable("VISION_INSPECTION_TOKEN")
    ?? throw new InvalidOperationException("VISION_INSPECTION_TOKEN is required");
using var client = new HttpClient { BaseAddress = new Uri(args.Length > 1 ? args[1] : "http://127.0.0.1:8765"), Timeout = TimeSpan.FromSeconds(30) };
client.DefaultRequestHeaders.Add("X-Vision-Token", token);
using var stream = File.OpenRead(args[0]);
using var body = new StreamContent(stream);
body.Headers.ContentType = new("application/octet-stream");
using var queued = await client.PostAsync("/v1/jobs/upload", body);
queued.EnsureSuccessStatusCode();
using var queuedJson = JsonDocument.Parse(await queued.Content.ReadAsStringAsync());
var jobId = queuedJson.RootElement.GetProperty("job_id").GetString();
for (var attempt = 0; attempt < 300; attempt++) {
    using var response = await client.GetAsync($"/v1/jobs/{Uri.EscapeDataString(jobId!)}");
    response.EnsureSuccessStatusCode();
    var text = await response.Content.ReadAsStringAsync();
    using var result = JsonDocument.Parse(text);
    var state = result.RootElement.GetProperty("state").GetString();
    if (state is not ("queued" or "running" or "delivery_pending")) {
        Console.WriteLine(text);
        return result.RootElement.GetProperty("verdict").GetString() == "REVIEW" ? 2 : 0;
    }
    await Task.Delay(200);
}
throw new TimeoutException("Inspection/field acknowledgment timed out; do not treat this as OK");
