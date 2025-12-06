# -*- coding: utf-8 -*-
"""
<<<<<<< HEAD
地表沉降监测系统后端服务 - 最终完整版
功能：
1. 智能识别全站仪(XYZ)/水准仪(Z)数据
2. 修复水准仪距离计算(默认10m间距)
3. 计算 W, i, K, U, ε 五大指标
4. 学术风格图片导出
5. 提供模板文件下载
=======
地表沉降监测系统后端服务
功能：提供数据解析、变形指标计算（沉降、水平位移、坡度、曲率）、以及自动化监测报告生成。
>>>>>>> ae1dbd034fe6ae1d3fb1b1446848b2ff0d0472d4
"""

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from typing import List, Dict, Any
import pandas as pd
import io
import os
import re
import math
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib

# 配置 Matplotlib 后端
matplotlib.use('Agg')
# 字体设置：优先使用 Times New Roman (英文) 和 SimHei/SimSun (中文)
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

app = FastAPI(title="地表监测分析系统 Pro")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 辅助函数 ---

def is_date_like(s: Any) -> bool:
    """判断字符串是否像日期"""
    s = str(s).strip()
    if not s or s.lower() == 'nan': return False
    return bool(re.search(r'\d{2,4}[/.-]\d{1,2}[/.-]\d{1,2}', s))

def clean_float(val: Any) -> float:
    try:
        return float(val)
    except:
        return 0.0

# --- 核心计算引擎 ---

def calculate_mechanics_v4(content: bytes):
    """
    智能解析 + 五大指标计算核心逻辑
    """
    # === 1. 读取 CSV ===
    df_raw = None
    for encoding in ['utf-8', 'gbk', 'gb18030']:
        try:
            df_raw = pd.read_csv(io.BytesIO(content), header=None, encoding=encoding)
            break
        except:
            continue
    if df_raw is None:
        raise ValueError("无法识别文件编码")

    # === 2. 智能判定表头位置与类型 ===
    header_row_idx = -1
    is_total_station = False
    
    # 扫描前20行
    for i in range(min(20, len(df_raw))):
        row_vals = [str(x).upper().strip() for x in df_raw.iloc[i].tolist()]
        cnt_x = row_vals.count('X')
        cnt_y = row_vals.count('Y')
        cnt_z = row_vals.count('Z')
        
        if cnt_x >= 2 and cnt_y >= 2 and cnt_z >= 2:
            header_row_idx = i
            is_total_station = True
            break
        if cnt_z >= 2:
            header_row_idx = i
            is_total_station = False
            
    if header_row_idx == -1: header_row_idx = 2

    # === 3. 建立列映射 ===
    header_row = [str(x).upper().strip() for x in df_raw.iloc[header_row_idx].tolist()]
    date_row_idx = header_row_idx - 1
    date_row = [str(x).strip() for x in df_raw.iloc[date_row_idx].tolist()] if date_row_idx >= 0 else []

    dates = []
    col_mappings = [] 
    
    if is_total_station:
        for i in range(len(header_row) - 2):
            if header_row[i] == 'X' and header_row[i+1] == 'Y' and header_row[i+2] == 'Z':
                d_str = date_row[i] if i < len(date_row) else f"Date_{i}"
                if is_date_like(d_str):
                    col_mappings.append({'date': d_str, 'x': i, 'y': i+1, 'z': i+2})
                    if d_str not in dates: dates.append(d_str)
    else:
        # 水准仪逻辑
        z_indices = [i for i, v in enumerate(header_row) if v == 'Z']
        d_candidates = [d for d in date_row if is_date_like(d)]
        count = min(len(z_indices), len(d_candidates))
        for i in range(count):
            d_str = d_candidates[i]
            col_mappings.append({'date': d_str, 'x': -1, 'y': -1, 'z': z_indices[i]})
            if d_str not in dates: dates.append(d_str)

    # === 4. 提取原始坐标数据 ===
    raw_data_map = {} 
    point_ids = []
    
    data_start_idx = header_row_idx + 1
    for _, row in df_raw.iloc[data_start_idx:].iterrows():
        pid = str(row[0]).strip()
        if not pid or pid.lower() == 'nan': continue
        point_ids.append(pid)
        
        for mapping in col_mappings:
            d = mapping['date']
            if d not in raw_data_map: raw_data_map[d] = {}
            try:
                z_val = clean_float(row[mapping['z']])
                x_val = clean_float(row[mapping['x']]) if mapping['x'] != -1 else 0.0
                y_val = clean_float(row[mapping['y']]) if mapping['y'] != -1 else 0.0
                raw_data_map[d][pid] = {"x": x_val, "y": y_val, "z": z_val}
            except:
                 raw_data_map[d][pid] = {"x": 0, "y": 0, "z": 0}

    # === 5. 距离计算 ===
    distances_to_first = []
    
    if point_ids:
        ref_date = dates[0]
        p0_data = raw_data_map.get(ref_date, {}).get(point_ids[0])
        p0_x, p0_y = 0.0, 0.0
        has_p0_coords = False
        
        if is_total_station and p0_data:
            p0_x, p0_y = p0_data["x"], p0_data["y"]
            has_p0_coords = True

        for i, pid in enumerate(point_ids):
            if i == 0:
                distances_to_first.append(0.0)
                continue
            
            calculated_dist = None
            
            # 全站仪尝试计算实际坐标距离
            if is_total_station and has_p0_coords:
                p_curr = raw_data_map.get(ref_date, {}).get(pid)
                if not p_curr: 
                    # 尝试从其他日期找坐标
                    for d in dates:
                        if pid in raw_data_map.get(d, {}):
                            p_curr = raw_data_map[d][pid]
                            break
                if p_curr:
                    dx = p_curr["x"] - p0_x
                    dy = p_curr["y"] - p0_y
                    calculated_dist = math.sqrt(dx*dx + dy*dy)
            
            # 水准仪或坐标缺失时，强制累加 10m
            if calculated_dist is None:
                prev_dist = distances_to_first[-1]
                calculated_dist = prev_dist + 10.0
                
            distances_to_first.append(calculated_dist)
            
    # === 6. 计算五大指标 ===
    results = {d: {'W': [], 'U': [], 'i': [], 'K': [], 'E': []} for d in dates}
    
    # 初始线段长度 (用于计算 E)
    initial_seg_lens = []
    d0 = dates[0]
    for i in range(len(point_ids)-1):
        if is_total_station:
            p1 = raw_data_map[d0].get(point_ids[i], {'x':0, 'y':0})
            p2 = raw_data_map[d0].get(point_ids[i+1], {'x':0, 'y':0})
            l = math.sqrt((p2['x']-p1['x'])**2 + (p2['y']-p1['y'])**2)
            initial_seg_lens.append(l if l > 0 else 10.0)
        else:
            initial_seg_lens.append(10.0)

    for d in dates:
        # A. 下沉 W
        for pid in point_ids:
            try:
                z0 = raw_data_map[d0][pid]['z']
                zt = raw_data_map[d][pid]['z']
                results[d]['W'].append(z0 - zt)
            except:
                results[d]['W'].append(0.0)
            
        # B. 水平移动 U
        for pid in point_ids:
            if is_total_station:
                try:
                    x0 = raw_data_map[d0][pid]['x']
                    xt = raw_data_map[d][pid]['x']
                    y0 = raw_data_map[d0][pid]['y']
                    yt = raw_data_map[d][pid]['y']
                    u = math.sqrt((xt-x0)**2 + (yt-y0)**2)
                    results[d]['U'].append(u)
                except:
                    results[d]['U'].append(0.0)
            else:
                results[d]['U'].append(0.0)

        # C. 倾斜 i
        i_list = []
        for j in range(len(point_ids)-1):
            dw = results[d]['W'][j+1] - results[d]['W'][j]
            dx = distances_to_first[j+1] - distances_to_first[j]
            i_val = (dw / dx) if dx > 0 else 0
            i_list.append(i_val)
        i_list.append(0) 
        results[d]['i'] = i_list
        
        # D. 水平变形 E
        e_list = []
        for j in range(len(point_ids)-1):
            if is_total_station:
                p_c = raw_data_map[d].get(point_ids[j], {'x':0, 'y':0})
                p_n = raw_data_map[d].get(point_ids[j+1], {'x':0, 'y':0})
                l_curr = math.sqrt((p_n['x']-p_c['x'])**2 + (p_n['y']-p_c['y'])**2)
                l_init = initial_seg_lens[j]
                eps = ((l_curr - l_init) / l_init) * 1000.0
            else:
                eps = 0.0
            e_list.append(eps)
        e_list.append(0)
        results[d]['E'] = e_list
        
        # E. 曲率 K
        k_list = [0.0]
        for j in range(1, len(point_ids)-1):
            di = i_list[j] - i_list[j-1]
            dx1 = distances_to_first[j] - distances_to_first[j-1]
            dx2 = distances_to_first[j+1] - distances_to_first[j]
            l_avg = (dx1 + dx2) / 2.0
            k_val = (di / l_avg) if l_avg > 0 else 0
            k_list.append(k_val)
        k_list.append(0.0)
        results[d]['K'] = k_list

    return {
        "is_total_station": is_total_station,
        "points": point_ids,
        "dates": dates,
        "dist_X": distances_to_first,
        "results": results
    }

def get_extremum_stats(data_pack):
    """统计极值"""
    stats = {}
    metrics_map = {
        'W': '下沉 (mm)', 'i': '倾斜 (mm/m)', 'K': '曲率 (mm/m²)', 
        'U': '水平移动 (mm)', 'E': '水平变形 (mm/m)'
    }
    
    for key, label in metrics_map.items():
        if not data_pack['is_total_station'] and key in ['U', 'E']:
            continue
            
        max_v, min_v = -99999.0, 99999.0
        max_p, min_p = "--", "--"
        
        for d in data_pack['dates']:
            vals = data_pack['results'][d][key]
            valid_indices = range(len(vals))
            # 排除首尾无效值
            if key in ['i', 'E']: valid_indices = range(len(vals)-1)
            if key == 'K': valid_indices = range(1, len(vals)-1)
            
            for idx in valid_indices:
                v = vals[idx]
                if v > max_v:
                    max_v = v
                    max_p = data_pack['points'][idx]
                    if key in ['i', 'E']: max_p += f"-{data_pack['points'][idx+1]}"
                if v < min_v:
                    min_v = v
                    min_p = data_pack['points'][idx]
                    if key in ['i', 'E']: min_p += f"-{data_pack['points'][idx+1]}"
        
        if max_p == "--": max_v, min_v = 0, 0
        
        stats[key] = {
            "label": label,
            "max": f"{max_v:.2f}", "max_pt": max_p,
            "min": f"{min_v:.2f}", "min_pt": min_p
        }
    return stats

# --- API 路由 ---

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = calculate_mechanics_v4(content)
        stats = get_extremum_stats(data)
        return {"code": 200, "data": data, "stats": stats}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export-chart")
async def export_chart(file: UploadFile = File(...), chart_type: str = Form(...)):
    """导出高分辨率学术图片"""
    try:
        content = await file.read()
        data = calculate_mechanics_v4(content)
        
        plt.figure(figsize=(10, 6), dpi=300)
        plt.style.use('fast')
        
        titles = {
            'W': 'Subsidence (下沉)', 'U': 'Horizontal Movement (水平移动)',
            'i': 'Tilt (倾斜)', 'K': 'Curvature (曲率)', 'E': 'Deformation (变形)'
        }
        
        key = chart_type
        
        for d in data['dates']:
            y_vals = data['results'][d][key]
            x_vals = data['dist_X']
            
            # 数据裁剪
            if key in ['i', 'E']:
                x_vals = x_vals[:-1]
                y_vals = y_vals[:-1]
            elif key == 'K':
                x_vals = x_vals[1:-1]
                y_vals = y_vals[1:-1]
                
            plt.plot(x_vals, y_vals, label=d, marker='o', markersize=3, linewidth=1.5)

        plt.title(titles.get(key, key), fontsize=14, fontweight='bold')
        plt.xlabel('Distance (m)', fontsize=12)
        plt.ylabel(f'{key} Value', fontsize=12)
        plt.legend(frameon=True, edgecolor='black', fancybox=False)
        plt.grid(linestyle='--', alpha=0.6)
        plt.tight_layout()

        img_io = io.BytesIO()
        plt.savefig(img_io, format='png')
        plt.close()
        img_io.seek(0)
        
        filename = f"Academic_Chart_{key}.png"
        return StreamingResponse(img_io, media_type="image/png", 
                                 headers={"Content-Disposition": f"attachment; filename={filename}"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# 新增：模板文件下载接口
@app.get("/files/{filename}")
async def download_file(filename: str):
    if filename not in ["全站仪.csv", "水准.csv"]:
        raise HTTPException(status_code=404, detail="文件不存在")
    if os.path.exists(filename):
        return FileResponse(filename)
    return HTTPException(status_code=404)

@app.get("/")
async def root():
    if os.path.exists("index.html"): return FileResponse("index.html")
    return "index.html not found"

if __name__ == "__main__":
<<<<<<< HEAD
    uvicorn.run(app, host="0.0.0.0", port=8001)
=======
    uvicorn.run(app, host=host, port=port)
>>>>>>> ae1dbd034fe6ae1d3fb1b1446848b2ff0d0472d4
