import requests
import concurrent.futures
import time

# 請替換為您 Flask 伺服器實際的位址與 Port
API_URL = "http://127.0.0.1:5000/api/doors/assign"
NUM_REQUESTS = 20  # 瞬間發起的請求數量

def send_assign_request(request_id):
    """發送單一包裹分派請求"""
    package_id = f"TEST_PKG_{request_id:03d}"
    payload = {"id": package_id}
    
    try:
        # 發送 POST 請求
        response = requests.post(API_URL, json=payload, timeout=5)
        return {
            "req_id": request_id,
            "status": response.status_code,
            "response": response.json() if response.ok else response.text
        }
    except requests.exceptions.RequestException as e:
        return {
            "req_id": request_id,
            "status": "Network Error",
            "response": str(e)
        }

def run_test():
    print(f"開始執行高併發壓力測試：瞬間發起 {NUM_REQUESTS} 個請求...")
    start_time = time.time()
    
    results = []
    # 使用 ThreadPoolExecutor 創造 20 個同時運作的執行緒
    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_REQUESTS) as executor:
        # 將任務塞入執行緒池，模擬瞬間併發
        futures = [executor.submit(send_assign_request, i) for i in range(1, NUM_REQUESTS + 1)]
        
        # 收集執行結果
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    end_time = time.time()
    
    # --- 整理與印出測試結果 ---
    print(f"\n測試完成！總耗時: {end_time - start_time:.3f} 秒\n")
    print("-" * 40)
    
    success_count = sum(1 for r in results if r["status"] == 200)
    error_400_count = sum(1 for r in results if r["status"] == 400)
    error_500_count = sum(1 for r in results if r["status"] == 500)
    
    for r in sorted(results, key=lambda x: x["req_id"]):
        if r["status"] == 500:
            print(f"請求 {r['req_id']:02d} | HTTP {r['status']} | 系統崩潰 (可能為 SQLite 鎖死)")
        elif r["status"] == 200:
            print(f"請求 {r['req_id']:02d} | HTTP {r['status']} | {r['response'].get('message', '')}")
        else:
            print(f"請求 {r['req_id']:02d} | HTTP {r['status']} | {r['response']}")

    print("-" * 40)
    print(f"統計結果：")
    print(f"  - 成功分配 (200): {success_count}")
    print(f"  - 擋車或無空門 (400/409): {error_400_count}")
    print(f"  - 系統內部錯誤 (500): {error_500_count}")

if __name__ == "__main__":
    run_test()