import subprocess, sys

files = [
    r"D:/微信文件/微信缓存/xwechat_files/wxid_ax309k0qts4n22_b9fa/msg/file/2026-04/1_TimeZ - 偶像万万岁_(Instrumental).wav",
    r"C:/Users/AnYu/Desktop/UVR输出/1_文夫 - 骗子 (1)_(Vocals).wav"
]

for f in files:
    print(f"=== {f} ===")
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration,bit_rate,size",
         "-show_entries", "stream=sample_rate,channels,bits_per_sample,codec_name",
         "-of", "default=noprint_wrappers=1", f],
        capture_output=True, text=True
    )
    print(result.stdout)
    print()
