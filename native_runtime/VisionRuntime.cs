using System;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
namespace ModuVision;
public sealed class Executor : IDisposable {
    const string Library="modu_vision_runtime";
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern IntPtr mv_create([MarshalAs(UnmanagedType.LPUTF8Str)] string package,[MarshalAs(UnmanagedType.LPUTF8Str)] string options,out IntPtr error);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern int mv_execute(IntPtr handle,[MarshalAs(UnmanagedType.LPUTF8Str)] string request,out IntPtr output);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern int mv_cancel(IntPtr handle);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern void mv_release(IntPtr handle);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern void mv_free(IntPtr text);
    IntPtr handle;
    readonly object gate=new();
    readonly object lifetime=new();
    int activeCalls;
    bool disposing;
    public Executor(string package,string options="{}") {handle=mv_create(package,options,out var error);if(handle==IntPtr.Zero)throw new InvalidOperationException(Read(error));}
    static string Read(IntPtr text){try{return Marshal.PtrToStringUTF8(text)??"";}finally{mv_free(text);}}
    IntPtr BeginCall(bool cancellation=false){lock(lifetime){if(handle==IntPtr.Zero||(disposing&&!cancellation))throw new ObjectDisposedException(nameof(Executor));activeCalls++;return handle;}}
    void EndCall(){lock(lifetime){activeCalls--;Monitor.PulseAll(lifetime);}}
    public string Execute(string request){lock(gate){var current=BeginCall();try{int status=mv_execute(current,request,out var output);var text=Read(output);if(status==1)throw new InvalidOperationException(text);return text;}finally{EndCall();}}}
    public string Predict(string image,string? imageId=null)=>Execute(JsonSerializer.Serialize(new {image_path=image,image_id=imageId}));
    public bool Cancel(){var current=BeginCall(cancellation:true);try{int status=mv_cancel(current);if(status<0)throw new InvalidOperationException("Cancellation is unavailable for this executor");return status==1;}finally{EndCall();}}
    public void Dispose(){lock(lifetime){disposing=true;while(activeCalls!=0)Monitor.Wait(lifetime);if(handle!=IntPtr.Zero){mv_release(handle);handle=IntPtr.Zero;}GC.SuppressFinalize(this);}}
}
public sealed class Predictor : IDisposable {
    readonly Executor executor;
    public Predictor(string package,string options="{}"){executor=new Executor(package,options);}
    public string Predict(string image,string? imageId=null)=>executor.Predict(image,imageId);
    public bool Cancel()=>executor.Cancel();
    public void Dispose()=>executor.Dispose();
}
public static class Program {
    public static int Main(string[] args){
        if(args.Length<2){Console.Error.WriteLine("Usage: VisionRuntime PACKAGE IMAGE [DEADLINE_MS] | PACKAGE --cancel-demo IMAGE");return 2;}
        Console.OutputEncoding=new System.Text.UTF8Encoding(false);
        if(args[1]=="--cancel-demo"){
            try{
                if(args.Length!=3)throw new ArgumentException("Cancellation demo requires PACKAGE --cancel-demo IMAGE");
                using var executor=new Executor(args[0],"{\"deadline_ms\":30000}");
                var running=Task.Run(()=>executor.Predict(args[2]));
                var budget=System.Diagnostics.Stopwatch.StartNew();bool requested=false;
                while(budget.ElapsedMilliseconds<10000){if(executor.Cancel()){requested=true;break;}if(running.IsCompleted)break;Thread.Sleep(1);}
                using var cancelled=JsonDocument.Parse(running.GetAwaiter().GetResult());
                if(!requested||!cancelled.RootElement.TryGetProperty("status",out var status)||status.GetString()!="cancelled")throw new InvalidOperationException("Inference completed before confirmed cancellation");
                if(executor.Cancel())throw new InvalidOperationException("Idle executor retained active cancellation");
                using var next=JsonDocument.Parse(executor.Predict(args[2]));
                Console.WriteLine(JsonSerializer.Serialize(new {cancelled=cancelled.RootElement,next=next.RootElement}));return 0;
            }catch(Exception error){Console.Error.WriteLine(error.Message);return 2;}
        }
        try{bool execute=args[1]=="--execute";int deadlineIndex=execute?3:2;using var executor=new Executor(args[0],args.Length>deadlineIndex?JsonSerializer.Serialize(new {deadline_ms=int.Parse(args[deadlineIndex])}):"{}");var result=execute?executor.Execute(System.IO.File.ReadAllText(args[2])):executor.Predict(args[1]);Console.WriteLine(result);return JsonDocument.Parse(result).RootElement.TryGetProperty("status",out var status)&&status.GetString()=="timeout"?3:0;}
        catch(Exception error){Console.Error.WriteLine(error.Message);return 2;}
    }
}
