// СВЕТЛАЯ ТЕМА: отдельная палитра поверх текущего акцента; изменения не требуют перезапуска.
(() => {
    const choice = document.getElementById('theme-choice'), save = document.getElementById('save-theme'), message = document.getElementById('theme-message');
    let saved = 'dark', current = 'dark';
    function apply(theme) {
        if (!['dark','light','transparent'].includes(theme)) return;
        current = theme; document.documentElement.dataset.theme = theme;
        choice.value = theme; applyAccent(currentAccent); window.playerVisuals?.themeChanged();
        document.dispatchEvent(new CustomEvent('theme-changed'));
    }
    function updateControls() {
        choice.disabled = !settingsReady || settingsSaving;
        save.disabled = choice.disabled || current === saved;
    }
    function load(data) {
        if (!['dark','light','transparent'].includes(data.theme ?? 'dark')) { message.textContent = 'Некорректная тема. Файл не перезаписывается.'; return; }
        saved = data.theme ?? 'dark'; apply(saved); updateControls(); message.textContent = 'Тема загружена.';
    }
    choice.addEventListener('change', () => { apply(choice.value); updateControls(); message.textContent = 'Предпросмотр. Нажмите «Сохранить тему», чтобы запомнить выбор.'; });
    save.addEventListener('click', async () => {
        if (save.disabled) return;
        settingsSaving = true; updateSettingsControls(); updateControls();
        try { const data = await window.pywebview.api.save_theme(current); saved = data.theme; message.textContent = 'Тема сохранена.'; }
        catch (error) { message.textContent = 'Не удалось сохранить тему: ' + error.message; }
        finally { settingsSaving = false; updateSettingsControls(); updateControls(); }
    });
    window.themeControls = { updateControls };
    window.addEventListener('visual-settings-loaded', event => load(event.detail));
    if (window.visualSettingsSnapshot) load(window.visualSettingsSnapshot);
    updateControls();
})();
