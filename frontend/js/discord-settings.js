(() => {
    const group = document.getElementById('discord-settings');
    const enabled = document.getElementById('discord-presence-enabled');
    const message = document.getElementById('discord-presence-message');
    let saved = false;

    function load(settings) {
        if (!settings.discord_presence_available || typeof window.pywebview?.api?.save_discord_presence !== 'function') return;
        group.hidden = false;
        saved = settings.discord_presence_enabled === true;
        enabled.checked = saved;
        enabled.disabled = false;
    }

    window.addEventListener('visual-settings-loaded', event => load(event.detail));
    if (window.visualSettingsSnapshot) load(window.visualSettingsSnapshot);

    enabled.addEventListener('change', async () => {
        enabled.disabled = true;
        try {
            const updated = await window.pywebview.api.save_discord_presence(enabled.checked);
            saved = updated.discord_presence_enabled === true;
            message.textContent = saved ? 'Показ в Discord включён.' : 'Показ в Discord выключен.';
        } catch {
            message.textContent = 'Не удалось сохранить настройку Discord.';
        } finally {
            enabled.checked = saved;
            enabled.disabled = false;
        }
    });
})();
