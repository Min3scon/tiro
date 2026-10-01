# Synthesize the rare-word test sentences with both installed SAPI voices.
Add-Type -AssemblyName System.Speech
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "tests\data\rare"
New-Item -ItemType Directory -Force $out | Out-Null
$cases = Get-Content (Join-Path $PSScriptRoot "rare_words.json") -Raw | ConvertFrom-Json
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$voices = @("Microsoft Zira Desktop", "Microsoft Hazel Desktop")
$i = 0
foreach ($c in $cases) {
    foreach ($v in $voices) {
        $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
        $s.SelectVoice($v)
        $s.Rate = 1
        $file = Join-Path $out ("{0:D2}_{1}.wav" -f $i, $v.Split(" ")[1].ToLower())
        $s.SetOutputToWaveFile($file, $fmt)
        $s.Speak($c.say)
        $s.SetOutputToNull()
        $s.Dispose()
    }
    $i++
}
Get-ChildItem $out | Measure-Object | Select-Object -ExpandProperty Count
