using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace CodexAccountManager.Core;

public sealed class JunctionService
{
    private const uint MountPoint = 0xA0000003;
    private const uint GetReparse = 0x000900A8;
    private const uint SetReparse = 0x000900A4;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string name, uint access, uint share,
        IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeviceIoControl(SafeFileHandle handle, uint code, byte[]? input,
        int inputSize, byte[]? output, int outputSize, out int returned, IntPtr overlapped);

    private static SafeFileHandle Open(string path, uint access = 0, uint share = 3)
    {
        var handle = CreateFileW(path, access, share, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
        if (handle.IsInvalid) { var error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new IOException($"Cannot open directory: {path}", new Win32Exception(error)); }
        return handle;
    }

    // Deny rename/removal while a directory is being inspected or traversed.
    public static SafeFileHandle PinDirectory(string path, bool allowWrites = false)
    {
        var handle = Open(path, access: 0x80000000, share: allowWrites ? 3u : 1u);
        try { PathSafety.OrdinaryDirectory(path); return handle; }
        catch { handle.Dispose(); throw; }
    }

    public void Create(string link, string target)
    {
        link = PathSafety.Canonical(link);
        target = PathSafety.Canonical(target);
        PathSafety.OrdinaryDirectory(target);
        PathSafety.OrdinaryDirectory(Path.GetDirectoryName(link)!);
        if (PathSafety.Exists(link)) throw new IOException("Junction destination already exists. Nothing replaced.");
        if (!string.Equals(new DriveInfo(Path.GetPathRoot(link)!).DriveFormat, "NTFS", StringComparison.OrdinalIgnoreCase))
            throw new IOException("Profiles must be stored on NTFS.");
        using var targetPin = PinDirectory(target);
        Directory.CreateDirectory(link);
        var substitute = Encoding.Unicode.GetBytes(@"\??\" + target);
        var print = Encoding.Unicode.GetBytes(target);
        var data = new byte[16 + substitute.Length + 2 + print.Length + 2];
        BitConverter.GetBytes(MountPoint).CopyTo(data, 0);
        BitConverter.GetBytes(checked((ushort)(data.Length - 8))).CopyTo(data, 4);
        BitConverter.GetBytes(checked((ushort)substitute.Length)).CopyTo(data, 10);
        BitConverter.GetBytes(checked((ushort)(substitute.Length + 2))).CopyTo(data, 12);
        BitConverter.GetBytes(checked((ushort)print.Length)).CopyTo(data, 14);
        substitute.CopyTo(data, 16);
        print.CopyTo(data, 18 + substitute.Length);
        using (var handle = Open(link, 0x40000000))
            if (!DeviceIoControl(handle, SetReparse, data, data.Length, null, 0, out _, IntPtr.Zero))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Unable to create NTFS junction. Profile retained for inspection.");
        Verify(link, target);
    }

    public string GetTarget(string link)
    {
        using var handle = Open(link);
        return GetTarget(handle);
    }

    private static string GetTarget(SafeFileHandle handle)
    {
        var buffer = new byte[16384];
        if (!DeviceIoControl(handle, GetReparse, null, 0, buffer, buffer.Length, out var length, IntPtr.Zero))
            throw new IOException("Sessions is not recognized as the expected NTFS junction.");
        if (length < 16 || BitConverter.ToUInt32(buffer, 0) != MountPoint)
            throw new IOException("Expected an NTFS directory junction, not another reparse type.");
        var offset = 16 + BitConverter.ToUInt16(buffer, 8);
        var count = BitConverter.ToUInt16(buffer, 10);
        if (count == 0 || count % 2 != 0 || offset + count > length)
            throw new IOException("Invalid junction data.");
        var target = Encoding.Unicode.GetString(buffer, offset, count);
        if (!target.StartsWith(@"\??\", StringComparison.Ordinal)) throw new IOException("Unsupported junction target.");
        return PathSafety.Canonical(target[4..]);
    }

    public void Verify(string link, string target)
    {
        PathSafety.OrdinaryDirectory(target);
        if (!PathSafety.Same(GetTarget(link), target)) throw new IOException("Junction target does not match configured shared sessions.");
    }

    public void Remove(string link, string target)
    {
        Verify(link, target);
        // Directory.Delete(false) removes only the directory entry, never traverses the target.
        Directory.Delete(link, false);
        if (PathSafety.Exists(link)) throw new IOException("Junction removal did not complete.");
        if (!Directory.Exists(target)) throw new IOException("Shared sessions verification failed after detaching junction.");
    }
}
