// pad_probe : tells what a pad is connected to (GND / 3.3V / nothing), using ESP32 GPIO 18 as a tester
// Wiring: one jumper wire from ESP32 pin 18 to the pad you want to test (touch ONE pad at a time).
// Needs: module powered (VDD -> 3V3, GND -> GND) and USB connected.
// Method: read the pin with the internal pull-up and with the internal pull-down.
//   both LOW            -> pad is tied to GND
//   both HIGH           -> pad is tied to 3.3V
//   pull-up HIGH, pull-down LOW -> pad is floating (connected to nothing)
// Do NOT use this on a pad that may carry 5V.
const int PIN_TEST = 18;

int readHigh(int mode) {
  pinMode(PIN_TEST, mode);
  delay(20);
  int hi = 0;
  for (int i = 0; i < 20; i++) { hi += digitalRead(PIN_TEST); delay(2); }
  return hi;            // 0..20
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { }
  Serial.println("=== pad probe start: touch ONE pad with the wire from GPIO18 ===");
}

void loop() {
  int up = readHigh(INPUT_PULLUP);
  int dn = readHigh(INPUT_PULLDOWN);
  const char* r;
  if (up <= 2 && dn <= 2)        r = "GND (tied to ground)";
  else if (up >= 18 && dn >= 18) r = "3.3V (tied to supply)";
  else if (up >= 18 && dn <= 2)  r = "FLOATING (connected to nothing)";
  else                           r = "UNSTABLE (bad contact)";
  Serial.printf("pull-up %d/20, pull-down %d/20  ->  %s\n", up, dn, r);
  delay(300);
}
