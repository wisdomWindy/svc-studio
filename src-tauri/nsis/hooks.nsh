; SVC Studio 安装钩子：确保桌面快捷方式使用应用图标（而不是继承旧缓存/旧图标）
;
; 背景：Windows 会缓存 .lnk 的图标位图；升级安装后快捷方式偶尔仍显示旧图标。
; 这里在安装完成后重建桌面与开始菜单快捷方式，显式把 IconLocation 指向 exe，
; 并通知 shell 刷新图标缓存。

!macro NSIS_HOOK_POSTINSTALL
  ; 桌面快捷方式：显式指定图标（exe 内嵌 icon.ico）
  Delete "$DESKTOP\${PRODUCTNAME}.lnk"
  CreateShortCut "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "" "$INSTDIR\${MAINBINARYNAME}.exe" 0

  ; 开始菜单快捷方式同样重建
  Delete "$SMPROGRAMS\${PRODUCTNAME}.lnk"
  CreateShortCut "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe" "" "$INSTDIR\${MAINBINARYNAME}.exe" 0

  ; 通知资源管理器刷新图标缓存（SHCNE_ASSOCCHANGED）
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, i 0, i 0)'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; 卸载不能删用户数据：把 data/ 挪到安装目录同级，重装时应用会自动搬回来
  ${If} ${FileExists} "$INSTDIR\data\*.*"
    RMDir /r "$INSTDIR-data"
    Rename "$INSTDIR\data" "$INSTDIR-data"
  ${EndIf}
!macroend