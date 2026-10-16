
const byte anemometerPin = 2;
const byte rainBucketPin = 3;
const byte windVanePin = A0;
const byte lightAnalogPin = A1;

const unsigned long anemometerDebounceMs = 8;
const unsigned long rainBucketDebounceMs = 100;

// Anemometer
volatile unsigned long anemometerCount = 0;
volatile bool anemometerArmed = true;
unsigned long anemometerReleaseStart = 0;

// Rain bucket
volatile unsigned long rainBucketCount = 0;
volatile bool rainBucketArmed = true;
unsigned long rainBucketReleaseStart = 0;

// Serial command buffer
char commandBuffer[16];
byte commandLength = 0;

// Wind vane
struct WindDirection {
  int measuredLow;
  int measuredHigh;
  int classificationLow;
  int classificationHigh;
  float degrees;
};

const WindDirection windDirections[] = {
  {788, 790, 778, 800,   0.0},
  {408, 409, 398, 419,  22.5},
  {464, 466, 454, 476,  45.0},
  { 84,  85,  77,  89,  67.5},
  { 94,  96,  90, 106,  90.0},
  { 66,  67,  56,  76, 112.5},
  {187, 188, 177, 198, 135.0},
  {127, 128, 117, 138, 157.5},
  {290, 291, 280, 301, 180.0},
  {244, 246, 234, 256, 202.5},
  {632, 634, 622, 644, 225.0},
  {599, 601, 589, 611, 247.5},
  {945, 948, 935, 958, 270.0},
  {828, 831, 818, 841, 292.5},
  {889, 890, 879, 900, 315.0},
  {704, 706, 694, 716, 337.5}
};

const int windDirectionCount =
  sizeof(windDirections) / sizeof(windDirections[0]);

float getWindDirection(int adc) {
  // Check defined classification ranges
  for (int i = 0; i < windDirectionCount; i++) {
    if (adc >= windDirections[i].classificationLow &&
        adc <= windDirections[i].classificationHigh) {
      return windDirections[i].degrees;
    }
  }

  // Outside ranges: choose nearest measured midpoint
  int closestIndex = 0;
  float closestDistance = 1024.0;

  for (int i = 0; i < windDirectionCount; i++) {
    float midpoint =
      (windDirections[i].measuredLow +
       windDirections[i].measuredHigh) / 2.0;

    float distance = abs(adc - midpoint);

    if (distance < closestDistance) {
      closestDistance = distance;
      closestIndex = i;
    }
  }

  return windDirections[closestIndex].degrees;
}

void setup() {
  Serial.begin(9600);

  pinMode(anemometerPin, INPUT_PULLUP);
  pinMode(rainBucketPin, INPUT_PULLUP);
  pinMode(windVanePin, INPUT);
  pinMode(lightAnalogPin, INPUT);

  attachInterrupt(
    digitalPinToInterrupt(anemometerPin),
    anemometerISR,
    FALLING
  );

  attachInterrupt(
    digitalPinToInterrupt(rainBucketPin),
    rainBucketISR,
    FALLING
  );
}

void loop() {
  unsigned long now = millis();

  // Anemometer: rearm after 8 ms continuously open
  if (!anemometerArmed) {
    if (digitalRead(anemometerPin) == HIGH) {
      if (anemometerReleaseStart == 0) {
        anemometerReleaseStart = now;
      }

      if (now - anemometerReleaseStart >= anemometerDebounceMs) {
        noInterrupts();
        anemometerArmed = true;
        interrupts();

        anemometerReleaseStart = 0;
      }
    } else {
      anemometerReleaseStart = 0;
    }
  }

  // Rain bucket: rearm after 100 ms continuously open
  if (!rainBucketArmed) {
    if (digitalRead(rainBucketPin) == HIGH) {
      if (rainBucketReleaseStart == 0) {
        rainBucketReleaseStart = now;
      }

      if (now - rainBucketReleaseStart >= rainBucketDebounceMs) {
        noInterrupts();
        rainBucketArmed = true;
        interrupts();

        rainBucketReleaseStart = 0;
      }
    } else {
      rainBucketReleaseStart = 0;
    }
  }

  // Process incoming serial commands
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (commandLength > 0) {
        commandBuffer[commandLength] = '\0';

        if (strcmp(commandBuffer, "READ") == 0) {
          sendReadings();
        }

        commandLength = 0;
      }
    } else if (commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = c;
    } else {
      commandLength = 0;
    }
  }
}

void sendReadings() {
  unsigned long pulses;
  unsigned long tips;

  // Snapshot and reset counters atomically
  noInterrupts();

  pulses = anemometerCount;
  tips = rainBucketCount;

  anemometerCount = 0;
  rainBucketCount = 0;

  interrupts();

  // Read wind direction
  int adc = analogRead(windVanePin);
  float direction = getWindDirection(adc);
  int lightAnalog = analogRead(lightAnalogPin);

  // Return JSON
  Serial.print("{\"anemometer_pulses\":");
  Serial.print(pulses);

  Serial.print(",\"rain_bucket_tips\":");
  Serial.print(tips);

  Serial.print(",\"wind_direction_deg\":");
  Serial.print(direction, 1);

  Serial.print(",\"light_analog\":");
  Serial.print(lightAnalog);

  Serial.println("}");
}

void anemometerISR() {
  if (anemometerArmed) {
    anemometerCount++;
    anemometerArmed = false;
  }
}

void rainBucketISR() {
  if (rainBucketArmed) {
    rainBucketCount++;
    rainBucketArmed = false;
  }
}
