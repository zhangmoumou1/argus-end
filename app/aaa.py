def create_fast_file(file_path, size_gb):
    """快速生成指定大小的空洞文件"""
    # 按照 GB 计算字节数，并用 int() 强制转为整数，防止小数报错
    size_bytes = int(size_gb * 1024 * 1024 * 1024)
    with open(file_path, 'wb') as f:
        f.seek(size_bytes - 1)
        f.write(b'\0')
    print(f"成功生成 {size_gb}GB 的空洞文件：{file_path}")
# 示例：生成一个 1.5GB 的文件
path = 'C:\\Users\\bytde\\Desktop\\对象存储资源\\视频\\test_1.01GB.bin'
create_fast_file(path, 1.01)
import os
print(f"文件实际大小: {os.path.getsize(path)} 字节")
