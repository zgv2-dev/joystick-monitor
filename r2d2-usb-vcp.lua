-- TNS|R2D2 USB-VCP|TNE

local AUTHOR = "Apachi Team"
local BUILD_DATETIME = "2025-11-03 13:00"

local CHANNEL_COUNT = 24
local SERIAL_BAUDRATE = 115200
local SERIAL_PREFIX = "$"
local UPDATE_INTERVAL_TICKS = 10
local BATT_VOLT = 0

---------------------------------------------------------
-- State
---------------------------------------------------------
local modelInfo
local mixesInfo = {}
local channelValues = {}
local render

---------------------------------------------------------
-- Channel & serial functions
---------------------------------------------------------
local function readChannels()
  for i = 1, CHANNEL_COUNT do
    channelValues[i] = getOutputValue(i - 1)
  end
end

local function readMixes()
  for i = 1, CHANNEL_COUNT do
    local m = model.getMix(i - 1, 0)
    local srcName = ""
    if m then
      if getSourceName and m.source then
        local ok, name = pcall(getSourceName, m.source)
        if ok and name then srcName = name end
      end
      local mix = mixesInfo[i] or {}
      mix.name = m.name or ""
      mix.src = srcName
      mixesInfo[i] = mix
    else
      local mix = mixesInfo[i] or {}
      mix.name = ""
      mix.src = ""
      mixesInfo[i] = mix
    end
  end
end

local function sendChannels()
  local data = SERIAL_PREFIX
  for i = 1, #channelValues do
    data = data .. channelValues[i] .. ","
  end
  serialWrite(data .. "\r\n")
end

---------------------------------------------------------
-- Drawing
---------------------------------------------------------
local function drawNothing() end

local function draw_bw128x64()
  lcd.clear()
  lcd.drawText(0, 0, modelInfo.name, SMLSIZE)
  lcd.drawText(55, 1, "R2D2", SMLSIZE)
  lcd.drawText(84, 0, string.format("Batt: %1.1fV", BATT_VOLT), SMLSIZE)

  lcd.drawFilledRectangle(0, 7, 128, 1)
  lcd.drawFilledRectangle(53, 0, 22, 7)

  for i = 1, CHANNEL_COUNT do
    local col = (i - 1) // 8
    local row = (i - 1) % 8
    local x = col * 42
    local y = 9 + row * 7

    lcd.drawText(x, y, string.format("%02d    %s", i, string.sub(mixesInfo[i].src, 3)), SMLSIZE)

    local w = (channelValues[i] + 1024 + 256) // 255 -- means divide by 8
    if w < 1 then w = 1 elseif w > 9 then w = 9 end
    lcd.drawFilledRectangle(x + 11, y, w, 6)
    lcd.drawPoint(x + 11 + 4, y + 2)
  end
end

local function draw_c480x272()
  local back = lcd.RGB(0, 0, 0)
  local front = lcd.RGB(255, 255, 255)
  local fTitle = MIDSIZE + front
  local fSmall = SMLSIZE + front

  lcd.clear(back)
  lcd.drawText(10, 10, "R2D2 USB-VCP", fTitle)
  lcd.drawText(240, 10, modelInfo.name, fTitle)
  lcd.drawText(10, 240, string.format("RC BATTERY: %.2fV", BATT_VOLT), fTitle)

  for i = 1, CHANNEL_COUNT do
    local v = (channelValues[i] // 2) + 1500
    local w = (channelValues[i] + 1024 + 34) // 34

    local colBase = (i <= 12) and 10 or 240
    local row = (i <= 12) and i or (i - 12)
    local y = 30 + row * 16

    lcd.drawText(colBase, y, string.format("CH%02d:%04d", i, v), fSmall)
    lcd.drawFilledRectangle(colBase, y + 14, w, 2, front)

    local mix = mixesInfo[i]
    if mix then
      lcd.drawText(colBase + 80, y, mix.src, fSmall)
      lcd.drawText(colBase + 140, y, mix.name, fSmall)
    end
  end
end

local function draw_c480x320()
  draw_c480x272()
end

local function draw_c800x480()
  local back = lcd.RGB(0, 0, 0)
  local front = lcd.RGB(255, 255, 255)
  local fTitle = MIDSIZE + front
  local fSmall = SMLSIZE + front

  lcd.clear(back)
  lcd.drawText(10, 10, "R2D2 USB-VCP", fTitle)
  lcd.drawText(400, 10, modelInfo.name, fTitle)
  lcd.drawText(10, 420, string.format("RC BATTERY: %.2fV", BATT_VOLT), fTitle)

  for i = 1, CHANNEL_COUNT do
    local v = (channelValues[i] // 2) + 1500
    local w = (channelValues[i] + 1024 + 34) // 34

    local colBase = (i <= 12) and 10 or 400
    local row = (i <= 12) and i or (i - 12)
    local y = 40 + row * 28

    lcd.drawText(colBase, y, string.format("CH%02d:%04d", i, v), fSmall)
    lcd.drawFilledRectangle(colBase, y + 21, w, 2, front)

    local mix = mixesInfo[i]
    if mix then
      lcd.drawText(colBase + 110, y, mix.src, fSmall)
      lcd.drawText(colBase + 190, y, mix.name, fSmall)
    end
  end
end

---------------------------------------------------------
-- Init & Run
---------------------------------------------------------
local function init()
  modelInfo = model.getInfo() or { name = "Unknown" }
  readMixes()
  setSerialBaudrate(SERIAL_BAUDRATE)

  -- Select Draw procedure
  if LCD_W >= 800 and LCD_H >= 480 then
    render = draw_c800x480
  elseif LCD_W >= 480 and LCD_H >= 320 then
    render = draw_c480x320
  elseif LCD_W >= 480 and LCD_H >= 272 then
    render = draw_c480x272
  elseif LCD_W >= 128 and LCD_H >= 64 then
    render = draw_bw128x64
  else
    render = drawNothing
  end

end

local function run()
  BATT_VOLT = getValue("tx-voltage")

  -- Pre-draw loop
  for i = 1, UPDATE_INTERVAL_TICKS do
    readChannels()
    sendChannels()
  end

  -- Draw
  render()

  -- Post-draw loop
  for i = 1, UPDATE_INTERVAL_TICKS do
    readChannels()
    sendChannels()
  end

  return 0
end

return { run = run, init = init }
