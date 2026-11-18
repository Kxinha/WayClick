#Requires AutoHotkey v2.0
toggle := false
F2:: {
    global toggle
    toggle := !toggle
    SetTimer(Spam, toggle ? 50 : 0)
}
Spam() {
    Send("{Space}")
}
