#include "esp_camera.h"
#include <WiFi.h>
#include "esp_http_server.h"

// ================= WIFI =================
struct WifiInfo {
    const char* ssid;
    const char* pass;
};

WifiInfo wifiList[] = {
    {"77E1", "06102004"},
    {"77BinhDinh", "06102004"},
    {"PTIT.HCM_SV", ""}
};

const int WIFI_COUNT = sizeof(wifiList) / sizeof(wifiList[0]);

// ================= AI THINKER PINS =================
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5

#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

httpd_handle_t stream_httpd = NULL;

// ================= STREAM HANDLER =================
static esp_err_t stream_handler(httpd_req_t *req)
{
    camera_fb_t *fb = NULL;
    esp_err_t res = ESP_OK;
    char part_buf[64];

    res = httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
    if (res != ESP_OK) return res;

    while (true)
    {
        fb = esp_camera_fb_get();
        if (!fb) continue;

        size_t hlen = snprintf(part_buf, sizeof(part_buf),
            "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
            fb->len);

        httpd_resp_send_chunk(req, "--frame\r\n", 9);
        httpd_resp_send_chunk(req, part_buf, hlen);
        httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
        httpd_resp_send_chunk(req, "\r\n", 2);

        esp_camera_fb_return(fb);
    }
    return res;
}

// ================= SERVER =================
void startCameraServer()
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 81;

    httpd_uri_t uri = {
        .uri = "/stream",
        .method = HTTP_GET,
        .handler = stream_handler,
        .user_ctx = NULL
    };

    httpd_start(&stream_httpd, &config);
    httpd_register_uri_handler(stream_httpd, &uri);
}

// ================= CAMERA INIT =================
bool initCamera()
{
    camera_config_t config;

    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;

    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;

    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;

    config.pin_sscb_sda = SIOD_GPIO_NUM;
    config.pin_sscb_scl = SIOC_GPIO_NUM;

    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;

    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;

    // --- Cấu hình chất lượng ảnh nét căng (VGA) ---
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 10;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.fb_count = psramFound() ? 3 : 1;

    return esp_camera_init(&config) == ESP_OK;
}

// ================= WIFI CONNECT =================
void connectWiFi()
{
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);

    for (int i = 0; i < WIFI_COUNT; i++)
    {
        WiFi.begin(wifiList[i].ssid, wifiList[i].pass);
        int retry = 0;
        
        while (WiFi.status() != WL_CONNECTED && retry < 20)
        {
            delay(500);
            retry++;
        }

        if (WiFi.status() == WL_CONNECTED)
            break;
            
        WiFi.disconnect(true);
    }
}

// ================= SETUP / LOOP =================
void setup()
{
    Serial.begin(115200);

    if (!initCamera()) {
        Serial.println("Khoi dong Camera that bai!");
        return;
    }

    connectWiFi();
    startCameraServer();

    Serial.print("STREAM: http://");
    Serial.print(WiFi.localIP());
    Serial.println(":81/stream");
}

void loop()
{
    delay(1000);
}