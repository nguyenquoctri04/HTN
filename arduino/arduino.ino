#include <Wire.h>
#include <LiquidCrystal_I2C.h>

#define MQ2_PIN A0
#define LED_PIN 6
#define BUZZER_PIN 7
#define RELAY_PIN 8

#define GAS_THRESHOLD 200

LiquidCrystal_I2C lcd(0x27, 16, 2);

bool isFire = false;
bool isManual = false;

void setup() {
  Serial.begin(9600);

  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(RELAY_PIN, OUTPUT);

  digitalWrite(LED_PIN, LOW);
  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(RELAY_PIN, LOW);

  lcd.init();
  lcd.backlight();

  lcd.setCursor(0, 0);
  lcd.print("He thong AIoT");
  lcd.setCursor(0, 1);
  lcd.print("San sang...");
  delay(2000);
}

void loop() {
  int gasValue = analogRead(MQ2_PIN);

  // ================= 1. NHẬN LỆNH TỪ PYTHON =================
  // Đọc sạch hàng đợi Serial để cập nhật trạng thái mới nhất từ AI/Web
  while (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();

    if (command.length() == 0) continue; // Bỏ qua nếu là chuỗi rỗng

    // ===== ĐIỀU KHIỂN THỦ CÔNG (TỪ DASHBOARD WEB) =====
    if (command.startsWith("CTRL:")) {
      isManual = true;
      int ledState = command.substring(5, 6).toInt();
      int buzState = command.substring(7, 8).toInt();
      int pumpState = command.substring(9, 10).toInt();

      digitalWrite(LED_PIN, ledState);
      digitalWrite(BUZZER_PIN, buzState);
      digitalWrite(RELAY_PIN, pumpState);

      updateLCD("CHE DO THU CONG", "Dieu khien Web");
    }
    // ===== AI PHÁT HIỆN CHÁY =====
    else if (command == "FIRE") {
      isManual = false;
      isFire = true;
    }
    // ===== AI BÁO AN TOÀN =====
    else if (command == "SAFE") {
      isManual = false;
      isFire = false;
    }
    // ===== HỆ THỐNG RESET =====
    else if (command == "RESET") {
      isManual = false;
      isFire = false;

      digitalWrite(LED_PIN, LOW);
      digitalWrite(BUZZER_PIN, LOW);
      digitalWrite(RELAY_PIN, LOW);

      updateLCD("HE THONG RESET", "Dang giam sat");
    }
  }

  // ================= 2. LOGIC CHẾ ĐỘ TỰ ĐỘNG =================
  if (!isManual) {
    bool isGasLeak = (gasValue > GAS_THRESHOLD);

    // TRƯỜNG HỢP KHẨN CẤP: CÓ CHÁY + RÒ RỈ GAS
    if (isFire && isGasLeak) {
      digitalWrite(LED_PIN, HIGH);
      digitalWrite(BUZZER_PIN, HIGH);
      digitalWrite(RELAY_PIN, HIGH);
      updateLCD("!!! KHAN CAP !!!", "Lua + Khi Gas");
    }
    // TRƯỜNG HỢP CHÁY: CÓ LỬA (AI) nhưng GAS bình thường
    else if (isFire && !isGasLeak) {
      digitalWrite(LED_PIN, HIGH);
      digitalWrite(BUZZER_PIN, HIGH);
      digitalWrite(RELAY_PIN, HIGH);
      updateLCD("BAO CHAY !!!", "Phun nuoc...");
    }
    // TRƯỜNG HỢP RÒ RỈ GAS: GAS vượt ngưỡng nhưng chưa có lửa
    else if (!isFire && isGasLeak) {
      digitalWrite(LED_PIN, HIGH);
      digitalWrite(BUZZER_PIN, HIGH);
      digitalWrite(RELAY_PIN, LOW); // Không bật máy bơm để tránh tia lửa điện từ motor gây nổ gas
      updateLCD("CANH BAO GAS", "Ro ri khi gas");
    }
    // TRƯỜNG HỢP AN TOÀN / BÌNH THƯỜNG
    else {
      digitalWrite(LED_PIN, LOW);
      digitalWrite(BUZZER_PIN, LOW);
      digitalWrite(RELAY_PIN, LOW);
      updateLCD("TRANG THAI", "Binh thuong");
    }
  }

  // ================= 3. ĐỒNG BỘ DỮ LIỆU LÊN PYTHON =================
  // Sử dụng bộ định thời millis() thay vì gửi liên tục ở mọi vòng lặp. 
  // Gửi dữ liệu cách nhau 200ms giúp Python không bị tràn bộ đệm xử lý.
  static unsigned long lastReport = 0;
  if (millis() - lastReport > 200) { 
    lastReport = millis();
    
    // Gửi chỉ số GAS
    Serial.print("GAS:");
    Serial.println(gasValue);
    
    // Gửi trạng thái thực tế của thiết bị (Đồng bộ ngược lên Web)
    Serial.print("STATE:");
    Serial.print(digitalRead(LED_PIN));
    Serial.print(",");
    Serial.print(digitalRead(BUZZER_PIN));
    Serial.print(",");
    Serial.println(digitalRead(RELAY_PIN));
  }

  // Giữ delay nhỏ để mạch nhận lệnh nhạy bén, không bị trễ dòng lệnh
  delay(20); 
}

// Hàm cập nhật LCD thông minh: Chỉ xóa và in lại khi nội dung thay đổi thực sự
void updateLCD(String line1, String line2) {
  static String old1 = "";
  static String old2 = "";

  // Nếu nội dung trùng với lần hiển thị trước thì bỏ qua (Tránh chớp màn hình & nghẽn CPU)
  if (line1 == old1 && line2 == old2) return;

  old1 = line1;
  old2 = line2;

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(line1);
  lcd.setCursor(0, 1);
  lcd.print(line2);
}