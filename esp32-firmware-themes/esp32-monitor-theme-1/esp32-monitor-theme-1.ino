#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>

// ---------------- Pinos ----------------
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

// 1 = mostra um pequeno texto discreto (uso/temp) abaixo de cada anel
#define SHOW_VALUES 0

// 1 = enquanto nenhum dado chegar pela serial, simula valores para testar o visual
#define DEMO_MODE 0

// ---------------- Cores ----------------
#define BG_COLOR     0x0000
#define PANEL_COLOR  0x1082
#define TRACK_COLOR  0x4208   // trilho dos anéis / barras vazias
#define GRID_COLOR   0x2965
#define MUTED_TEXT   0x8410

#define CPU_COLOR    0x05FF   // ciano
#define GPU_COLOR    0xB7F6   // verde
#define RAM_COLOR    0xFDEF   // laranja
#define WARN_COLOR   0xFFE0   // amarelo
#define BAD_COLOR    0xFACB   // vermelho

// ---------------- Layout ----------------
#define CPU_X   40
#define GPU_X   120
#define RAM_X   200
#define RING_Y  68          // was 62 (moved down for bigger rings)
#define TEMP_Y  114         // was 108
#define VAL_Y   124         // was 120

#define RING_R      38      // was 32  -> ring is bigger
#define RING_T      10      // was 5   -> ring band is thicker
#define RING_SEGS   135
#define RING_START  135
#define RING_SWEEP  270

// Gráfico de histórico
#define GRAPH_POINTS 72
#define GX 12
#define GY 164
#define GW 216
#define GH 140

// ---------------- Dados ----------------
float cpuTemp = 0, cpuUsage = 0;
float gpuTemp = 0, gpuUsage = 0;
float ramUsage = 0;
bool gotData = false;

float cpuHistory[GRAPH_POINTS];
float gpuHistory[GRAPH_POINTS];
int historyIndex = 0;
bool historyFull = false;

// ---------------- Anéis ----------------
struct Ring {
  int cx, cy;
  int lastSeg;
  uint16_t lastColor;
};

Ring cpuRing = {CPU_X, RING_Y, 0, 0};
Ring gpuRing = {GPU_X, RING_Y, 0, 0};
Ring ramRing = {RAM_X, RING_Y, 0, 0};

float cosT[RING_SEGS];
float sinT[RING_SEGS];

// ---------------- Estado de animação ----------------
float fanAngle = 0;
bool blinkOn = true;
int lastRamBlocks = -1;

unsigned long lastGraphUpdate = 0;
unsigned long lastAnim = 0;
unsigned long lastBlink = 0;
const unsigned long GRAPH_INTERVAL = 1000;
const unsigned long ANIM_INTERVAL  = 40;
const unsigned long BLINK_INTERVAL = 500;

// ---------------- Protótipos ----------------
void parseData(String data);
void drawStatic();
void updateScreen();
void updateGraph();

// ---------------- Cores por estado ----------------
uint16_t usageColor(float usage, uint16_t accent) {
  if (usage >= 90) return BAD_COLOR;
  if (usage >= 70) return WARN_COLOR;
  return accent;
}

uint16_t tempColor(float temp, uint16_t accent) {
  if (temp >= 85) return BAD_COLOR;
  if (temp >= 70) return WARN_COLOR;
  return accent;
}

// ---------------- Anel (gauge de 270°) ----------------
void drawSeg(const Ring &r, int i, uint16_t c) {
  int x1 = r.cx + cosT[i] * (RING_R - RING_T);
  int y1 = r.cy + sinT[i] * (RING_R - RING_T);
  int x2 = r.cx + cosT[i] * RING_R;
  int y2 = r.cy + sinT[i] * RING_R;
  tft.drawLine(x1, y1, x2, y2, c);
}

// Só redesenha os segmentos que mudaram (ou o anel todo se a cor mudar)
void updateRing(Ring &r, float usage, uint16_t accent) {
  int seg = (int)(usage * RING_SEGS / 100.0f + 0.5f);
  uint16_t col = usageColor(usage, accent);

  if (col != r.lastColor) {
    for (int i = 0; i < RING_SEGS; i++) {
      drawSeg(r, i, i < seg ? col : TRACK_COLOR);
    }
  } else if (seg > r.lastSeg) {
    for (int i = r.lastSeg; i < seg; i++) drawSeg(r, i, col);
  } else if (seg < r.lastSeg) {
    for (int i = seg; i < r.lastSeg; i++) drawSeg(r, i, TRACK_COLOR);
  }

  r.lastSeg = seg;
  r.lastColor = col;
}

// ---------------- Ícones (centrados no anel) ----------------
void drawCpuFrame() {
  int cx = CPU_X, cy = RING_Y;

  tft.drawRoundRect(cx - 12, cy - 12, 25, 25, 3, CPU_COLOR);
  tft.drawRoundRect(cx - 11, cy - 11, 23, 23, 3, CPU_COLOR);

  for (int i = -6; i <= 6; i += 6) {
    tft.fillRect(cx + i - 1, cy - 17, 3, 5, CPU_COLOR);
    tft.fillRect(cx + i - 1, cy + 12, 3, 5, CPU_COLOR);
    tft.fillRect(cx - 17, cy + i - 1, 5, 3, CPU_COLOR);
    tft.fillRect(cx + 12, cy + i - 1, 5, 3, CPU_COLOR);
  }

  // Static die core — always CPU_COLOR now
  tft.fillRect(cx - 6, cy - 6, 13, 13, CPU_COLOR);
}

void drawGpuFrame() {
  int cx = GPU_X, cy = RING_Y;

  // Thick body
  tft.drawRoundRect(cx - 18, cy - 13, 37, 27, 3, GPU_COLOR);
  tft.drawRoundRect(cx - 17, cy - 12, 35, 25, 3, GPU_COLOR);

  // Thick fan ring (two circles)
  tft.drawCircle(cx, cy, 10, GPU_COLOR);
  tft.drawCircle(cx, cy, 9,  GPU_COLOR);

  // PCIe connector
  tft.fillRect(cx - 11, cy + 15, 23, 3, GPU_COLOR);
}

void drawRamFrame() {
  int cx = RAM_X, cy = RING_Y;

  // Thick stick body
  tft.drawRoundRect(cx - 17, cy - 10, 35, 19, 2, RAM_COLOR);
  tft.drawRoundRect(cx - 16, cy - 9,  33, 17, 2, RAM_COLOR);

  // Thicker gold-finger contacts
  for (int i = 0; i < 5; i++) {
    tft.fillRect(cx - 13 + i * 6, cy + 9, 3, 4, RAM_COLOR);
  }
}

void drawGpuFan() {
  int cx = GPU_X, cy = RING_Y;
  tft.fillCircle(cx, cy, 8, BG_COLOR);   // clear hub

  for (int i = 0; i < 3; i++) {
    float a = fanAngle + i * 2.094f;
    int x2 = cx + cosf(a) * 8;
    int y2 = cy + sinf(a) * 8;
    tft.drawLine(cx, cy,     x2, y2,     GPU_COLOR);
    tft.drawLine(cx, cy + 1, x2, y2 + 1, GPU_COLOR);
  }
  tft.fillCircle(cx, cy, 2, GPU_COLOR);  // axle
}

void updateRamBlocks() {
  int blocks = (int)(ramUsage / 25.0f + 0.5f);
  if (blocks == lastRamBlocks) return;
  lastRamBlocks = blocks;

  for (int i = 0; i < 4; i++) {
    tft.fillRect(RAM_X - 13 + i * 7, RING_Y - 6, 5, 12,
                 i < blocks ? RAM_COLOR : TRACK_COLOR);
  }
}

// ---------------- Barra de temperatura ----------------
void drawTempBar(int cx, float temp, uint16_t accent) {
  const int w = 64, h = 7;               // was 56 x 4
  int x = cx - w / 2;

  tft.fillRoundRect(x, TEMP_Y, w, h, 3, TRACK_COLOR);

  float t = constrain((temp - 30.0f) / 70.0f, 0.0f, 1.0f);
  int fw = (int)(w * t);
  uint16_t col = tempColor(temp, accent);
  if (temp >= 85 && !blinkOn) col = TRACK_COLOR;

  if (fw >= 6)      tft.fillRoundRect(x, TEMP_Y, fw, h, 3, col);
  else if (fw > 0)  tft.fillRect(x, TEMP_Y, fw, h, col);
}

#if SHOW_VALUES
void drawValues(int cx, float usage, float temp, bool hasTemp) {
  char buf[16];
  if (hasTemp) snprintf(buf, sizeof(buf), "%d%% %dC", (int)(usage + 0.5f), (int)(temp + 0.5f));
  else         snprintf(buf, sizeof(buf), "%d%%", (int)(usage + 0.5f));

  tft.fillRect(cx - 38, VAL_Y, 76, 8, BG_COLOR);
  tft.setTextSize(1);
  tft.setTextColor(MUTED_TEXT, BG_COLOR);

  int16_t x1, y1;
  uint16_t w, h;
  tft.getTextBounds(buf, 0, VAL_Y, &x1, &y1, &w, &h);
  tft.setCursor(cx - w / 2, VAL_Y);
  tft.print(buf);
}
#endif

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);
  Serial.setTimeout(20);   // evita travar em linhas incompletas
  delay(500);

  tftSPI.begin(TFT_SCK, TFT_MISO, TFT_MOSI, TFT_CS);
  tft.init(SCREEN_W, SCREEN_H);
  tft.setSPISpeed(40000000);
  tft.invertDisplay(false);
  tft.setRotation(2);

  for (int i = 0; i < RING_SEGS; i++) {
    float a = (RING_START + i * (RING_SWEEP / (float)RING_SEGS)) * DEG_TO_RAD;
    cosT[i] = cosf(a);
    sinT[i] = sinf(a);
  }

  for (int i = 0; i < GRAPH_POINTS; i++) {
    cpuHistory[i] = 0;
    gpuHistory[i] = 0;
  }

  drawStatic();
  updateScreen();
  Serial.println("ESP32 PC Monitor ready.");
}

// ---------------- Loop ----------------
void loop() {
  unsigned long now = millis();

  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      parseData(line);
      updateScreen();
    }
  }

#if DEMO_MODE
  static unsigned long lastDemo = 0;
  if (!gotData && now - lastDemo >= 200) {
    lastDemo = now;
    float t = now / 1000.0f;
    cpuUsage = 50 + 50 * sinf(t * 0.5f);
    gpuUsage = 50 + 50 * sinf(t * 0.35f + 1);
    ramUsage = 50 + 50 * sinf(t * 0.2f + 2);
    cpuTemp  = 60 + 35 * sinf(t * 0.3f);
    gpuTemp  = 60 + 35 * sinf(t * 0.25f + 1);
    updateScreen();
  }
#endif

  // Animação da ventoinha
  if (now - lastAnim >= ANIM_INTERVAL) {
    lastAnim = now;
    fanAngle += 0.04f + gpuUsage * 0.004f;
    if (fanAngle > 6.2832f) fanAngle -= 6.2832f;
    drawGpuFan();
  }

  // Pisca da barra de temperatura crítica
  if (now - lastBlink >= BLINK_INTERVAL) {
    lastBlink = now;
    blinkOn = !blinkOn;
    if (cpuTemp >= 85) drawTempBar(CPU_X, cpuTemp, CPU_COLOR);
    if (gpuTemp >= 85) drawTempBar(GPU_X, gpuTemp, GPU_COLOR);
  }

  // Histórico
  if (now - lastGraphUpdate >= GRAPH_INTERVAL) {
    lastGraphUpdate = now;
    cpuHistory[historyIndex] = cpuUsage;
    gpuHistory[historyIndex] = gpuUsage;
    historyIndex++;
    if (historyIndex >= GRAPH_POINTS) {
      historyIndex = 0;
      historyFull = true;
    }
    updateGraph();
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
    gotData = true;

    cpuUsage = constrain(cpuUsage, 0, 100);
    gpuUsage = constrain(gpuUsage, 0, 100);
    ramUsage = constrain(ramUsage, 0, 100);
  }
}

// ---------------- Desenho estático ----------------
void drawStatic() {
  tft.fillScreen(BG_COLOR);

  drawCpuFrame();
  drawGpuFrame();
  drawRamFrame();

  // Painel do gráfico
  tft.fillRoundRect(5, 140, 230, 174, 7, PANEL_COLOR);

  // Legenda: apenas dois pontos coloridos
  tft.fillCircle(18, 152, 3, CPU_COLOR);
  tft.fillCircle(32, 152, 3, GPU_COLOR);
}

// ---------------- Atualização dos elementos ----------------
void updateScreen() {
  updateRing(cpuRing, cpuUsage, CPU_COLOR);
  updateRing(gpuRing, gpuUsage, GPU_COLOR);
  updateRing(ramRing, ramUsage, RAM_COLOR);

  updateRamBlocks();

  drawTempBar(CPU_X, cpuTemp, CPU_COLOR);
  drawTempBar(GPU_X, gpuTemp, GPU_COLOR);

#if SHOW_VALUES
  drawValues(CPU_X, cpuUsage, cpuTemp, true);
  drawValues(GPU_X, gpuUsage, gpuTemp, true);
  drawValues(RAM_X, ramUsage, 0, false);
#endif
}

// ---------------- Gráfico ----------------
void drawHistory(const float *hist, uint16_t color) {
  int n = historyFull ? GRAPH_POINTS : historyIndex;
  if (n < 2) return;
  int start = historyFull ? historyIndex : 0;

  for (int i = 1; i < n; i++) {
    int p = (start + i - 1) % GRAPH_POINTS;
    int c = (start + i) % GRAPH_POINTS;

    int x1 = GX + ((i - 1) * GW) / (GRAPH_POINTS - 1);
    int x2 = GX + (i * GW)     / (GRAPH_POINTS - 1);
    int y1 = GY + GH - (int)(hist[p] * GH / 100.0f);
    int y2 = GY + GH - (int)(hist[c] * GH / 100.0f);

    // 3 px thick: draw the center line plus one above and one below
    for (int dy = -1; dy <= 1; dy++) {
      tft.drawLine(x1, y1 + dy, x2, y2 + dy, color);
    }
  }
}

void updateGraph() {
  tft.fillRect(GX, GY - 2, GW, GH + 5, PANEL_COLOR);

  for (int k = 0; k <= 4; k++) {
    int y = GY + (GH * k) / 4;
    tft.drawFastHLine(GX, y, GW, GRID_COLOR);
    tft.drawFastHLine(GX, y + 1, GW, GRID_COLOR);   // 2 px grid
  }

  drawHistory(cpuHistory, CPU_COLOR);
  drawHistory(gpuHistory, GPU_COLOR);
}
