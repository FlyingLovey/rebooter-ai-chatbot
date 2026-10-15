import uvicorn

if __name__ == "__main__":
    # Backend รันที่พอร์ต 8000 ส่วน Guard Service รันที่ 8001 (ดู README หลัก)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
