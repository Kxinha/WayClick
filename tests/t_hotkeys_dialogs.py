"""Os caminhos da aba Atalhos que passam por diálogo (Novo, Editar, passos,
Importar AHK, Remover), com os diálogos respondidos sozinhos. Offscreen e
isolado, ver harness.py."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h
from harness import a, check
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QFileDialog
a.CFG = "/tmp/wayclick-test-dialogs.json"
json.dump({"sound": False, "language": "pt_BR"}, open(a.CFG, "w"))
app = QApplication(sys.argv)
w = a.App(); w.sound.setChecked(False)
a.MacroDialog.exec = lambda self: QDialog.Accepted
w._new_macro()
check("New adds a macro", len(w.macros) == 1 and w.macros[0]["steps"][0]["code"] == a.KEYS["E"])
def edit_exec(self):
    self.mode.setCurrentIndex(self.mode.findData("hold"))
    a.StepDialog.exec = lambda s: QDialog.Accepted
    self.step_list.setCurrentRow(0)
    self._add_step(); self._edit_step(); self._move_step(1); self._move_step(-1)
    self._del_step()
    return QDialog.Accepted
a.MacroDialog.exec = edit_exec
w._edit_macro()
check("Edit changes mode and keeps steps", w.macros[0]["mode"] == "hold" and len(w.macros[0]["steps"]) == 1)
d = a.MacroDialog(w, {"steps": []}); d._ok()
check("OK without steps is refused", d.error.text() != "" and d.result() == 0, d.error.text())
d2 = a.MacroDialog(w, {"mode": "always", "period_ms": 1, "steps": [{"op": "tap", "code": 18}]}); d2._ok()
check("Always under 10 ms is refused", d2.error.text() != "")
QFileDialog.getOpenFileName = lambda *x, **k: (os.path.join(os.path.dirname(os.path.abspath(__file__)), "ahk", "combo.ahk"), "")
a.ImportDialog.exec = lambda self: QDialog.Accepted
w._import_ahk()
check("Import through the dialog path", len(w.macros) == 4, str(len(w.macros)))
QMessageBox.question = lambda *x, **k: QMessageBox.Yes
w.macro_list.setCurrentRow(0); w._remove_macro()
check("Remove asks and removes", len(w.macros) == 3)
QMessageBox.warning = lambda *x, **k: None
check("unreadable file is reported, not raised", w.import_ahk_path("/nonexistent/x.ahk") == 0)
w._mouse_changed(0)
check("changing mouse keeps the engine alive", w.engine.active and w.mouse is not None)
w.key_box.setChecked(True); w.key_box.setChecked(False)
check("keyboard kept while macros need it", w.keyboard is not None)
w.retranslate()
w.cleanup()
cfg = json.load(open(a.CFG))
check("saved", len(cfg["macros"]) == 3)
sys.exit(h.finish())
