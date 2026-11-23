"""Adapt the pinned pywebview WinForms backend for the packaged CoreCLR."""
import hashlib
from importlib.metadata import distribution


WINFORMS_SHA256 = 'e3962a59e74532d9266f08d1efb086519877b2d322ea83900036f59794db4b4d'

# pywebview 6.2.1 reflects over .NET Framework internals during import.
# In modern WinForms the public dialog already supports selecting multiple folders.
FOLDER_DIALOG = '''class OpenFolderDialog:
    @staticmethod
    def show(parent=None, initialDirectory=None, allow_multiple=False, title=None):
        dialog = WinForms.FolderBrowserDialog()
        try:
            dialog.InitialDirectory = initialDirectory or ''
            dialog.Multiselect = allow_multiple
            if title:
                dialog.Description = title
                dialog.UseDescriptionForTitle = True
            result = dialog.ShowDialog(parent) if parent is not None else dialog.ShowDialog()
            if result == WinForms.DialogResult.OK:
                return tuple(dialog.SelectedPaths)
            return None
        finally:
            dialog.Dispose()
'''


def patch_winforms(path):
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != WINFORMS_SHA256:
        raise RuntimeError('The pinned pywebview WinForms backend changed. Review the CoreCLR patch.')
    source = original.decode('utf-8')
    start = source.index('class OpenFolderDialog:')
    end = source.index('_main_window_created = Event()', start)
    source = source[:start] + FOLDER_DIALOG + '\n\n' + source[end:]
    source = source.replace(
        'from System import Array, Environment, Func, Int32, IntPtr, Object, Type, UInt32',
        'from System import Environment, Func, Int32, IntPtr, Type',
    ).replace('from System.Reflection import Assembly, BindingFlags  # noqa: E402\n', '')
    path.write_bytes(source.encode('utf-8'))


if __name__ == '__main__':
    path = distribution('pywebview').locate_file('webview/platforms/winforms.py')
    patch_winforms(path)
