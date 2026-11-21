(() => {
    const choice = document.getElementById('theme-choice');
    const message = document.getElementById('theme-message');
    const writer = createSettingsWriter(value => window.pywebview.api.save_theme(value), apply);

    function apply(theme) {
        choice.value = theme;
        document.documentElement.dataset.theme = theme;
        applyAccent(currentAccent);
        document.dispatchEvent(new CustomEvent('theme-changed'));
    }
    function load(data) {
        const theme = data.theme ?? 'dark';
        if (!['dark', 'light', 'transparent'].includes(theme)) return;
        writer.setSaved(theme);
        apply(theme);
    }

    choice.addEventListener('change', () => {
        apply(choice.value);
        writer.save(choice.value, message);
    });
    window.themeControls = { updateControls() { choice.disabled = !settingsReady; } };
    window.addEventListener('visual-settings-loaded', event => load(event.detail));
    if (window.visualSettingsSnapshot) load(window.visualSettingsSnapshot);
})();
