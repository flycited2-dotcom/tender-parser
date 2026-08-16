Option Explicit
Dim shell, folder, command
Set shell = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
command = "pythonw -m tender_parser control-center --open-browser"
shell.Run command, 0, False
