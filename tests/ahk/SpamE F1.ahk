#NoEnv
SendMode Input
SetBatchLines, -1

toggle := false

F1::
toggle := !toggle
if (toggle)
    SetTimer, SpamE, 1
else
    SetTimer, SpamE, Off
return

SpamE:
Send, e
return
