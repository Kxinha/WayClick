#Requires AutoHotkey v2.0
#SingleInstance Force
global running := false
F1::ToggleSpam()
ToggleSpam() {
    global running
    running := !running
    SetTimer(SpamKey, running ? 1 : 0)
}
SpamKey() {
    SendInput("{e}")
}
