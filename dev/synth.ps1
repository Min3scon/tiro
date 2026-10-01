# Synthesize test sentences with Windows voices.
#   synth.ps1 -Json items.json -Out folder [-Voices zira,hazel,george]
# items.json: [{"id": "...", "say": "..."}]. Writes <id>_<voice>.wav (SAPI voices: 16 kHz mono 16-bit;
# OneCore voices: whatever the engine produces; the Python side resamples).
param(
    [Parameter(Mandatory = $true)][string]$Json,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Voices = "zira,hazel,george"
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $Out | Out-Null
$items = Get-Content $Json -Raw -Encoding UTF8 | ConvertFrom-Json
$wanted = $Voices.Split(",") | ForEach-Object { $_.Trim().ToLower() }

Add-Type -AssemblyName System.Speech
$sapi = @{ "zira" = "Microsoft Zira Desktop"; "hazel" = "Microsoft Hazel Desktop" }
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

# OneCore (WinRT) voices, e.g. George
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}
[void][Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
$onecore = @{}
foreach ($v in [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices) {
    $onecore[$v.DisplayName.Split(" ")[1].ToLower()] = $v
}

$n = 0
foreach ($item in $items) {
    foreach ($voice in $wanted) {
        $file = Join-Path $Out ("{0}_{1}.wav" -f $item.id, $voice)
        if (Test-Path $file) { continue }
        if ($sapi.ContainsKey($voice)) {
            $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $s.SelectVoice($sapi[$voice])
            $s.Rate = 0
            $s.SetOutputToWaveFile($file, $fmt)
            $s.Speak([string]$item.say)
            $s.SetOutputToNull()
            $s.Dispose()
        }
        elseif ($onecore.ContainsKey($voice)) {
            $s = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
            $s.Voice = $onecore[$voice]
            $stream = Await ($s.SynthesizeTextToStreamAsync([string]$item.say)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
            $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
            [void](Await ($reader.LoadAsync([uint32]$stream.Size)) ([uint32]))
            $bytes = New-Object byte[] ([int]$stream.Size)
            $reader.ReadBytes($bytes)
            [IO.File]::WriteAllBytes($file, $bytes)
            $reader.Dispose(); $stream.Dispose(); $s.Dispose()
        }
        else { throw "unknown voice $voice" }
        $n++
    }
}
Write-Output "synthesized $n files into $Out"
