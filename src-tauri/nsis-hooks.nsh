; SVC Studio 卸载钩子
;
; 项目（数据集 / 配置 / 模型）默认放在安装目录的 projects 下，
; 卸载会连同它们一起删掉。这里在删除前明确警告，允许用户取消。

!macro NSIS_HOOK_PREUNINSTALL
  IfSilent done
  IfFileExists "$INSTDIR\projects\*.*" 0 done
  MessageBox MB_YESNO|MB_ICONEXCLAMATION "卸载将删除安装目录下的 projects 文件夹。$\r$\n$\r$\n里面包含数据集、配置和训练好的模型，删除后无法恢复。$\r$\n$\r$\n如需保留，请先点「否」，把 projects 文件夹复制到别处，再重新卸载。$\r$\n$\r$\n确定继续卸载吗？" IDNO cancel
  Goto done
  cancel:
    Abort
  done:
!macroend
