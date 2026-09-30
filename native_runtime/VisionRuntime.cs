using System;
using System.Runtime.InteropServices;
using System.Text.Json;
namespace ModuVision;
public sealed class Executor : IDisposable {
    const string Library="modu_vision_runtime";
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern IntPtr mv_create([MarshalAs(UnmanagedType.LPUTF8Str)] string package,[MarshalAs(UnmanagedType.LPUTF8Str)] string options,out IntPtr error);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern int mv_execute(IntPtr handle,[MarshalAs(UnmanagedType.LPUTF8Str)] string request,out IntPtr output);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern void mv_release(IntPtr handle);
    [DllImport(Library,CallingConvention=CallingConvention.Cdecl)] static extern void mv_free(IntPtr text);
    IntPtr handle;
    readonly object gate=new();
    public Executor(string package,string options="{}") {handle=mv_create(package,options,out var error);if(handle==IntPtr.Zero)throw new InvalidOperationException(Read(error));}
    static string Read(IntPtr text){try{return Marshal.PtrToStringUTF8(text)??"";}finally{mv_free(text);}}
    public string Execute(string request){lock(gate){if(handle==IntPtr.Zero)throw new ObjectDisposedException(nameof(Executor));int status=mv_execute(handle,request,out var output);var text=Read(output);if(status==1)throw new InvalidOperationException(text);return text;}}
    public string Predict(string image,string? imageId=null)=>Execute(JsonSerializer.Serialize(new {image_path=image,image_id=imageId}));
    public void Dispose(){lock(gate){if(handle!=IntPtr.Zero){mv_release(handle);handle=IntPtr.Zero;}GC.SuppressFinalize(this);}}
}
public sealed class Predictor : IDisposable {
    readonly Executor executor;
    public Predictor(string package,string options="{}"){executor=new Executor(package,options);}
    public string Predict(string image,string? imageId=null)=>executor.Predict(image,imageId);
    public void Dispose()=>executor.Dispose();
}
public static class Program {
    public static int Main(string[] args){
        if(args.Length<2){Console.Error.WriteLine("Usage: VisionRuntime PACKAGE IMAGE [DEADLINE_MS]");return 2;}
        try{bool execute=args[1]=="--execute";int deadlineIndex=execute?3:2;using var executor=new Executor(args[0],args.Length>deadlineIndex?JsonSerializer.Serialize(new {deadline_ms=int.Parse(args[deadlineIndex])}):"{}");var result=execute?executor.Execute(System.IO.File.ReadAllText(args[2])):executor.Predict(args[1]);Console.WriteLine(result);return JsonDocument.Parse(result).RootElement.TryGetProperty("status",out var status)&&status.GetString()=="timeout"?3:0;}
        catch(Exception error){Console.Error.WriteLine(error.Message);return 2;}
    }
}
