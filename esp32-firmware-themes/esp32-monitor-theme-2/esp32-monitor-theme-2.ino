#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>

#define TFT_CS    7
#define TFT_DC    0
#define TFT_RST   1
#define TFT_SCK   4
#define TFT_MOSI  6
#define TFT_MISO  5

SPIClass tftSPI(FSPI);
Adafruit_ST7789 tft(&tftSPI, TFT_CS, TFT_DC, TFT_RST);

#define SCREEN_W 240
#define SCREEN_H 320

// Colors
#define BG_COLOR       0x0841
#define PANEL_COLOR    0x1082
#define PANEL_DARK     0x0861
#define TEXT_COLOR     ST77XX_WHITE
#define MUTED_TEXT     0x8410
#define GRID_COLOR     0x39C7

#define CPU_COLOR      0x05FF
#define GPU_COLOR      0x07E0
#define RAM_COLOR      0xFD20

#define GOOD_COLOR     ST77XX_WHITE
#define WARN_COLOR     0xFFE0
#define BAD_COLOR      0xF800

// ---- Gauge config ----
#define GAUGE_RADIUS     20
#define GAUGE_START_DEG  135.0
#define GAUGE_END_DEG    405.0
#define GAUGE_SWEEP      (GAUGE_END_DEG - GAUGE_START_DEG)

#define CPU_GAUGE_X 195
#define CPU_GAUGE_Y 63
#define GPU_GAUGE_X 195
#define GPU_GAUGE_Y 124

unsigned long lastAnimUpdate = 0;
const unsigned long ANIM_INTERVAL = 50;   // ~20 fps

float cpuTemp  = 0.0;
float cpuUsage = 0.0;
float gpuTemp  = 0.0;
float gpuUsage = 0.0;
float ramUsage = 0.0;

float lastCpuTemp = -1.0;
float lastGpuTemp = -1.0;
float lastRamUsage = -1.0;
uint16_t lastStatusColor = GOOD_COLOR;

#define GRAPH_POINTS 54

float cpuHistory[GRAPH_POINTS];
float gpuHistory[GRAPH_POINTS];

int historyIndex = 0;
bool historyFull = false;

unsigned long lastGraphUpdate = 0;
const unsigned long GRAPH_INTERVAL = 1000;

// Forward declarations
void drawStatic();
void updateScreen();
void updateGraph();
void parseData(String data);

void drawCPUCard();
void drawGPUCard();
void drawRAMCard();

void updateCPUCard();
void updateGPUCard();
void updateRAMCard();

void drawUsageBar(int x, int y, int width, int height, float usage, uint16_t color);
void drawCenteredText(const char* text, int y, uint8_t size, uint16_t color);

void drawArcDots(int cx, int cy, int r, float startDeg, float endDeg,
                 uint16_t color, int dotR, float stepDeg);
void drawGauge(int cx, int cy, float usage, uint16_t color);
void updateGauges();

uint16_t getTempColor(float temp);
uint16_t getUsageColor(float usage);


void setup() {
  Serial.begin(115200);
  delay(500);

  tftSPI.begin(TFT_SCK, TFT_MISO, TFT_MOSI, TFT_CS);
  tft.init(240, 320);
  tft.invertDisplay(false);
  tft.setRotation(0);
  tft.fillScreen(BG_COLOR);

  for (int i = 0; i < GRAPH_POINTS; i++) {
    cpuHistory[i] = 0;
    gpuHistory[i] = 0;
  }

  drawStatic();

  lastCpuTemp = cpuTemp;
  lastGpuTemp = gpuTemp;
  lastRamUsage = ramUsage;

  Serial.println("ESP32 PC Monitor ready.");
}


void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      parseData(line);
      updateScreen();
    }
  }

  // Atualização do gráfico de histórico
  if (millis() - lastGraphUpdate >= GRAPH_INTERVAL) {
    lastGraphUpdate = millis();
    cpuHistory[historyIndex] = cpuUsage;
    gpuHistory[historyIndex] = gpuUsage;
    historyIndex++;
    if (historyIndex >= GRAPH_POINTS) {
      historyIndex = 0;
      historyFull = true;
    }
    updateGraph();
  }

  // Animação dos gauges (independente do envio de dados)
  if (millis() - lastAnimUpdate >= ANIM_INTERVAL) {
    lastAnimUpdate = millis();
    updateGauges();
  }
}


// cpuTemp,cpuUsage,gpuTemp,gpuUsage,ramUsage
void parseData(String data) {
  int idx1 = data.indexOf(',');
  int idx2 = data.indexOf(',', idx1 + 1);
  int idx3 = data.indexOf(',', idx2 + 1);
  int idx4 = data.indexOf(',', idx3 + 1);

  if (idx1 > 0 && idx2 > idx1 && idx3 > idx2 && idx4 > idx3) {
    cpuTemp  = data.substring(0, idx1).toFloat();
    cpuUsage = data.substring(idx1 + 1, idx2).toFloat();
    gpuTemp  = data.substring(idx2 + 1, idx3).toFloat();
    gpuUsage = data.substring(idx3 + 1, idx4).toFloat();
    ramUsage = data.substring(idx4 + 1).toFloat();

    cpuUsage = constrain(cpuUsage, 0, 100);
    gpuUsage = constrain(gpuUsage, 0, 100);
    ramUsage = constrain(ramUsage, 0, 100);
  }
}


void drawStatic() {
  tft.fillScreen(BG_COLOR);
  tft.fillRect(0, 0, SCREEN_W, 30, PANEL_COLOR);

  drawCenteredText("https://github.com/ffelipeoliveira/", 5, 1, TEXT_COLOR);
  drawCenteredText("esp32_st7789_monitor", 15, 1, TEXT_COLOR);

  drawCPUCard();
  drawGPUCard();
  drawRAMCard();

  tft.fillRoundRect(5, 218, 230, 96, 7, PANEL_COLOR);

  tft.setTextSize(1);
  tft.setTextColor(TEXT_COLOR, PANEL_COLOR);
  tft.setCursor(12, 224);
  tft.print("LOAD HISTORY");

  tft.setTextColor(CPU_COLOR, PANEL_COLOR);
  tft.setCursor(125, 224);
  tft.print("CPU");

  tft.setTextColor(GPU_COLOR, PANEL_COLOR);
  tft.setCursor(160, 224);
  tft.print("GPU");

  for (int y = 244; y <= 300; y += 14) {
    tft.drawFastHLine(10, y, 220, GRID_COLOR);
  }

  tft.setTextColor(MUTED_TEXT, PANEL_COLOR);
  tft.setCursor(12, 237);
  tft.print("100");
  tft.setCursor(16, 296);
  tft.print("0");
}


// ---------- CARDS ----------

void drawCPUCard() {
  tft.fillRoundRect(5, 36, 230, 55, 7, PANEL_COLOR);
  tft.fillRoundRect(5, 36, 5, 55, 3, CPU_COLOR);

  tft.setTextSize(1);
  tft.setTextColor(CPU_COLOR, PANEL_COLOR);
  tft.setCursor(14, 43);
  tft.print("CPU");

  // Gauge inicial vazio
  drawGauge(CPU_GAUGE_X, CPU_GAUGE_Y, 0, CPU_COLOR);
}

void drawGPUCard() {
  tft.fillRoundRect(5, 97, 230, 55, 7, PANEL_COLOR);
  tft.fillRoundRect(5, 97, 5, 55, 3, GPU_COLOR);

  tft.setTextSize(1);
  tft.setTextColor(GPU_COLOR, PANEL_COLOR);
  tft.setCursor(14, 104);
  tft.print("GPU");

  drawGauge(GPU_GAUGE_X, GPU_GAUGE_Y, 0, GPU_COLOR);
}

void drawRAMCard() {
  tft.fillRoundRect(5, 158, 230, 55, 7, PANEL_COLOR);
  tft.fillRoundRect(5, 158, 5, 55, 3, RAM_COLOR);

  tft.setTextSize(1);
  tft.setTextColor(RAM_COLOR, PANEL_COLOR);
  tft.setCursor(14, 165);
  tft.print("RAM");
}


// ---------- UPDATES ----------

void updateCPUCard() {
  // apenas a temperatura é atualizada (o gauge é redrawado pela animação)
  tft.fillRect(14, 57, 90, 24, PANEL_COLOR);
  tft.setTextColor(getTempColor(cpuTemp), PANEL_COLOR);
  tft.setTextSize(2);
  tft.setCursor(14, 57);
  tft.print(cpuTemp, 1);
  tft.print("C");
}

void updateGPUCard() {
  tft.fillRect(14, 118, 90, 24, PANEL_COLOR);
  tft.setTextColor(getTempColor(gpuTemp), PANEL_COLOR);
  tft.setTextSize(2);
  tft.setCursor(14, 118);
  tft.print(gpuTemp, 1);
  tft.print("C");
}

void updateRAMCard() {
  tft.fillRect(14, 180, 60, 24, PANEL_COLOR);
  tft.setTextColor(TEXT_COLOR, PANEL_COLOR);
  tft.setTextSize(2);
  tft.setCursor(14, 180);
  tft.print(ramUsage, 0);
  tft.print("%");

  drawUsageBar(70, 181, 150, 14, ramUsage, getUsageColor(ramUsage));
}


void updateScreen() {
  if (abs(cpuTemp - lastCpuTemp) >= 0.1) {
    updateCPUCard();
    lastCpuTemp = cpuTemp;
  }

  if (abs(gpuTemp - lastGpuTemp) >= 0.1) {
    updateGPUCard();
    lastGpuTemp = gpuTemp;
  }

  if (abs(ramUsage - lastRamUsage) >= 0.5) {
    updateRAMCard();
    lastRamUsage = ramUsage;
  }

  uint16_t newStatus = GOOD_COLOR;
  if (cpuTemp >= 85 || gpuTemp >= 85 || cpuUsage >= 95 || gpuUsage >= 95)
    newStatus = BAD_COLOR;
  else if (cpuTemp >= 75 || gpuTemp >= 75 || cpuUsage >= 80 || gpuUsage >= 80)
    newStatus = WARN_COLOR;

  if (newStatus != lastStatusColor) {
    tft.fillCircle(226, 15, 5, newStatus);
    lastStatusColor = newStatus;
  }
}


// ---------- GAUGE (animação) ----------

void drawArcDots(int cx, int cy, int r, float startDeg, float endDeg,
                 uint16_t color, int dotR, float stepDeg) {
  if (endDeg < startDeg) return;
  for (float a = startDeg; a <= endDeg; a += stepDeg) {
    float rad = a * PI / 180.0;
    int x = cx + (int)(cos(rad) * r + 0.5);
    int y = cy + (int)(sin(rad) * r + 0.5);
    tft.fillCircle(x, y, dotR, color);
  }
}

void drawGauge(int cx, int cy, float usage, uint16_t color) {
  usage = constrain(usage, 0, 100);

  unsigned long t = millis();

  // Limpa a área do gauge
  int cr = GAUGE_RADIUS + 6;
  tft.fillRect(cx - cr, cy - cr, cr * 2, cr * 2, PANEL_COLOR);

  // Arco de fundo (anel apagado)
  drawArcDots(cx, cy, GAUGE_RADIUS,
              GAUGE_START_DEG, GAUGE_END_DEG,
              PANEL_DARK, 2, 4.0);

  // Arco preenchido proporcional ao uso
  float endAngle = GAUGE_START_DEG + (usage / 100.0) * GAUGE_SWEEP;

  if (usage > 1) {
    drawArcDots(cx, cy, GAUGE_RADIUS,
                GAUGE_START_DEG, endAngle,
                color, 2, 4.0);
  }

  // Highlight giratório percorrendo o arco preenchido
  if (usage > 5) {
    float tPhase = (t % 800) / 800.0;                 // 0..1 a cada 800ms
    float hlAngle = GAUGE_START_DEG + tPhase * (endAngle - GAUGE_START_DEG);
    float hlRad = hlAngle * PI / 180.0;
    int hx = cx + (int)(cos(hlRad) * GAUGE_RADIUS + 0.5);
    int hy = cy + (int)(sin(hlRad) * GAUGE_RADIUS + 0.5);
    tft.fillCircle(hx, hy, 3, ST77XX_WHITE);
  }

  // Ponto pulsante na ponta do arco
  if (usage > 1) {
    float pulse = (sin((t % 942) / 150.0) + 1.0) * 0.5;   // 0..1
    int leadR = 3 + (int)(pulse * 1.5);                    // 3..4
    float lrad = endAngle * PI / 180.0;
    int lx = cx + (int)(cos(lrad) * GAUGE_RADIUS + 0.5);
    int ly = cy + (int)(sin(lrad) * GAUGE_RADIUS + 0.5);
    tft.fillCircle(lx, ly, leadR, color);
  }
}

void updateGauges() {
  drawGauge(CPU_GAUGE_X, CPU_GAUGE_Y, cpuUsage, getUsageColor(cpuUsage));
  drawGauge(GPU_GAUGE_X, GPU_GAUGE_Y, gpuUsage, getUsageColor(gpuUsage));
}


// ---------- Barra (só usada na RAM agora) ----------

void drawUsageBar(int x, int y, int width, int height,
                  float usage, uint16_t color) {
  usage = constrain(usage, 0, 100);

  tft.fillRoundRect(x, y, width, height, height / 2, PANEL_DARK);

  int fillWidth = (width * usage) / 100.0;
  if (fillWidth > 0) {
    tft.fillRoundRect(x, y, fillWidth, height, height / 2, color);
  }
}


// ---------- Gráfico de histórico ----------

void updateGraph() {
  tft.fillRect(25, 238, 202, 64, PANEL_COLOR);

  for (int y = 244; y <= 300; y += 14) {
    tft.drawFastHLine(25, y, 202, GRID_COLOR);
  }

  int points = historyFull ? GRAPH_POINTS : historyIndex;
  if (points < 2) return;

  for (int i = 1; i < points; i++) {
    int previousIndex = historyFull ? (historyIndex + i - 1) % GRAPH_POINTS : i - 1;
    int currentIndex  = historyFull ? (historyIndex + i)     % GRAPH_POINTS : i;

    int x1 = 25 + ((i - 1) * 202) / (GRAPH_POINTS - 1);
    int x2 = 25 + (i       * 202) / (GRAPH_POINTS - 1);

    int y1 = 300 - (cpuHistory[previousIndex] * 56 / 100);
    int y2 = 300 - (cpuHistory[currentIndex]  * 56 / 100);
    tft.drawLine(x1, y1, x2, y2, CPU_COLOR);

    y1 = 300 - (gpuHistory[previousIndex] * 56 / 100);
    y2 = 300 - (gpuHistory[currentIndex]  * 56 / 100);
    tft.drawLine(x1, y1, x2, y2, GPU_COLOR);
  }
}


// ---------- Helpers ----------

uint16_t getTempColor(float temp) {
  if (temp >= 85) return BAD_COLOR;
  if (temp >= 70) return WARN_COLOR;
  return GOOD_COLOR;
}

uint16_t getUsageColor(float usage) {
  if (usage >= 90) return BAD_COLOR;
  if (usage >= 70) return WARN_COLOR;
  return GOOD_COLOR;
}

void drawCenteredText(const char* text, int y, uint8_t size, uint16_t color) {
  tft.setTextSize(size);
  tft.setTextColor(color, PANEL_COLOR);

  int16_t x1, y1;
  uint16_t w, h;
  tft.getTextBounds(text, 0, y, &x1, &y1, &w, &h);

  int x = (SCREEN_W - w) / 2;
  tft.setCursor(x, y);
  tft.print(text);
}