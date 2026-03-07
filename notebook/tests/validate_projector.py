"""
============================================================
验证脚本：比较 Python 解析投影器 vs SIMIND MC 输出
============================================================

用途：确保 Python 投影器的输出与 SIMIND 物理上一致
方法：对同一个体模，两种方法各生成投影，比较差异

运行顺序：
  1. 先用 SIMIND 跑完一个 case（比如 case_0000）
  2. 运行此脚本比较结果
"""

import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import sys

# 导入投影器
sys.path.insert(0, '.')
from hybrid_pipeline import SPECTProjector


def load_simind_projections(a00_file, n_proj=60, nx=128, ny=128):
    """读取 SIMIND .a00 投影文件"""
    data = np.fromfile(a00_file, dtype=np.float32)
    expected = n_proj * nx * ny
    
    if len(data) != expected:
        print(f"警告：文件大小 {len(data)} != 预期 {expected}")
        print(f"  尝试 nx={nx}, ny={ny} 的其他投影数...")
        n_proj_actual = len(data) // (nx * ny)
        print(f"  实际投影数: {n_proj_actual}")
        data = data[:n_proj_actual * nx * ny]
        n_proj = n_proj_actual
    
    return data.reshape(n_proj, ny, nx)


def compare_projections(
    simind_a00: str,
    activity_file: str,
    attenuation_file: str,
    scatter_fraction: float = 0.35,
    output_dir: str = 'validation_output',
):
    """
    比较 SIMIND 和 Python 投影器的输出
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. 加载 SIMIND 结果
    print("加载 SIMIND 投影...")
    simind_proj = load_simind_projections(simind_a00)
    print(f"  形状: {simind_proj.shape}")
    print(f"  范围: [{simind_proj.min():.6f}, {simind_proj.max():.6f}]")
    
    # 2. 加载体模
    print("加载体模...")
    if activity_file.endswith('.npz'):
        activity = np.load(activity_file)['arr_0'].astype(np.float32)
        attenuation = np.load(attenuation_file)['arr_0'].astype(np.float32)
    elif activity_file.endswith('.bin'):
        activity = np.fromfile(activity_file, dtype=np.float32).reshape(128, 128, 128)
        attenuation = np.fromfile(attenuation_file, dtype=np.float32).reshape(128, 128, 128)
    
    # 3. Python 投影
    print("运行 Python 投影器...")
    projector = SPECTProjector(
        n_projections=simind_proj.shape[0],
        scatter_fraction=scatter_fraction,
    )
    python_proj = projector.forward_project(activity, attenuation, add_scatter=True)
    print(f"  形状: {python_proj.shape}")
    print(f"  范围: [{python_proj.min():.6f}, {python_proj.max():.6f}]")
    
    # 4. 归一化比较（两者的绝对尺度不同，比较相对分布）
    simind_norm = simind_proj / (simind_proj.max() + 1e-10)
    python_norm = python_proj / (python_proj.max() + 1e-10)
    
    # 5. 计算指标
    # 选几个代表性投影角度比较
    angles_to_compare = [0, 15, 30, 45]  # 投影索引
    
    print("\n" + "="*60)
    print("投影对比结果")
    print("="*60)
    
    for idx in angles_to_compare:
        if idx >= simind_proj.shape[0]:
            continue
        
        s = simind_norm[idx]
        p = python_norm[idx]
        
        # 相关系数
        corr = np.corrcoef(s.flatten(), p.flatten())[0, 1]
        
        # NRMSE
        nrmse = np.sqrt(np.mean((s - p)**2)) / (s.max() - s.min() + 1e-10)
        
        # SSIM 简化版（结构相似度）
        mu_s, mu_p = s.mean(), p.mean()
        std_s, std_p = s.std(), p.std()
        cov_sp = np.mean((s - mu_s) * (p - mu_p))
        C1, C2 = 0.01**2, 0.03**2
        ssim = ((2*mu_s*mu_p + C1) * (2*cov_sp + C2)) / \
               ((mu_s**2 + mu_p**2 + C1) * (std_s**2 + std_p**2 + C2))
        
        print(f"\n  投影 #{idx} (角度 {projector.angles[idx]:.0f}°):")
        print(f"    相关系数 (Correlation): {corr:.4f}")
        print(f"    NRMSE:                  {nrmse:.4f}")
        print(f"    SSIM:                   {ssim:.4f}")
        
        # 判断标准
        if corr > 0.95:
            print(f"    ✅ 相关性优秀")
        elif corr > 0.85:
            print(f"    ⚠️ 相关性良好（散射模型可能需要调整）")
        else:
            print(f"    ❌ 相关性差（检查投影几何是否匹配）")
    
    # 6. 可视化
    fig, axes = plt.subplots(3, 4, figsize=(16, 12))
    
    for col, idx in enumerate(angles_to_compare[:4]):
        if idx >= simind_proj.shape[0]:
            continue
        
        axes[0, col].imshow(simind_norm[idx], cmap='hot')
        axes[0, col].set_title(f'SIMIND #{idx}')
        axes[0, col].axis('off')
        
        axes[1, col].imshow(python_norm[idx], cmap='hot')
        axes[1, col].set_title(f'Python #{idx}')
        axes[1, col].axis('off')
        
        diff = simind_norm[idx] - python_norm[idx]
        axes[2, col].imshow(diff, cmap='RdBu', vmin=-0.3, vmax=0.3)
        axes[2, col].set_title(f'Diff #{idx}')
        axes[2, col].axis('off')
    
    plt.suptitle('SIMIND MC vs Python Analytical Projector', fontsize=14)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'comparison.png'), dpi=150)
    print(f"\n可视化已保存到 {output_dir}/comparison.png")
    
    # 7. 投影剖面对比
    fig2, axes2 = plt.subplots(2, 2, figsize=(12, 10))
    mid = simind_proj.shape[1] // 2  # 中间行
    
    for col, idx in enumerate(angles_to_compare[:4]):
        if idx >= simind_proj.shape[0]:
            continue
        ax = axes2[col // 2, col % 2]
        ax.plot(simind_norm[idx, mid, :], 'b-', label='SIMIND MC', linewidth=2)
        ax.plot(python_norm[idx, mid, :], 'r--', label='Python Analytical', linewidth=2)
        ax.set_title(f'Profile at projection #{idx}')
        ax.legend()
        ax.set_xlabel('Pixel')
        ax.set_ylabel('Normalized intensity')
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'profiles.png'), dpi=150)
    print(f"剖面图已保存到 {output_dir}/profiles.png")
    
    return {
        'simind_proj': simind_proj,
        'python_proj': python_proj,
    }


import os

if __name__ == '__main__':
    # 修改这些路径为你的实际文件
    compare_projections(
        simind_a00='case_0000.a00',                    # SIMIND 输出
        activity_file='case_0000_act_av.bin',          # 活度图
        attenuation_file='case_0000_atn_av.bin',       # 衰减图
        scatter_fraction=0.35,                          # 从 SIMIND .res 文件读取
        output_dir='validation_output',
    )
