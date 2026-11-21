(() => {
    const tablist = document.querySelector('#screen-settings .settings-tabs');
    const tabs = [...tablist.querySelectorAll('[role=tab]')];
    const technical = document.getElementById('settings-tab-technical');
    const experiments = document.getElementById('settings-tab-tester');
    let activeTab = tabs[0];

    function selectTab(tab) {
        activeTab = tab;
        for (const item of tabs) {
            const selected = item === tab;
            item.setAttribute('aria-selected', String(selected));
            item.tabIndex = selected ? 0 : -1;
            document.getElementById(item.getAttribute('aria-controls')).hidden = !selected;
        }
    }

    for (const tab of tabs) tab.addEventListener('click', () => selectTab(tab));
    tablist.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const visible = tabs.filter(tab => !tab.hidden);
        const index = visible.indexOf(activeTab);
        let next;
        if (event.key === 'Home') next = visible[0];
        else if (event.key === 'End') next = visible.at(-1);
        else next = visible[(index + visible.length + (event.key === 'ArrowRight' ? 1 : -1)) % visible.length];
        selectTab(next);
        next.focus();
    });

    const tester = document.getElementById('tester-mode');
    const testerMessage = document.getElementById('tester-mode-message');
    const miniMode = document.getElementById('mini-background-mode');
    const miniMessage = document.getElementById('mini-background-message');
    const miniLiquid = document.getElementById('tester-mini-liquid');
    const miniLiquidMessage = document.getElementById('tester-mini-message');
    const miniExperiment = document.getElementById('mini-experiment-active');
    const testerWriter = createSettingsWriter(enabled => window.pywebview.api.save_tester_mode(enabled), applyTester);
    const miniWriter = createSettingsWriter(value => window.pywebview.api.save_mini_background(value), applyMiniMode);
    const liquidWriter = createSettingsWriter(enabled => window.pywebview.api.save_mini_liquid(enabled), applyMiniLiquid);
    window.miniBackgroundMode = 'classic';

    function applyTester(enabled) {
        tester.checked = enabled;
        window.testerModeEnabled = enabled;
        experiments.hidden = !enabled;
        miniLiquid.disabled = !enabled;
        if (!enabled && activeTab === experiments) {
            selectTab(technical);
            tester.focus();
        }
        updateMiniMode();
        window.dispatchEvent(new Event('tester-mode-changed'));
    }

    function applyMiniMode(value) {
        miniMode.value = value;
        updateMiniMode();
    }

    function applyMiniLiquid(enabled) {
        miniLiquid.checked = enabled;
        updateMiniMode();
    }

    function updateMiniMode() {
        const experimental = tester.checked && miniLiquid.checked;
        miniExperiment.hidden = !experimental;
        miniMode.disabled = !settingsReady || experimental;
        window.miniBackgroundMode = experimental ? 'liquid' : miniMode.value;
        publish();
    }

    function load(data) {
        const enabled = data.tester_mode ?? false;
        const mode = data.mini_background === 'liquid' ? 'colors' : data.mini_background ?? 'classic';
        const liquid = data.mini_liquid ?? (data.mini_background === 'liquid');
        testerWriter.setSaved(enabled);
        miniWriter.setSaved(mode);
        liquidWriter.setSaved(liquid);
        tester.disabled = false;
        miniMode.value = mode;
        miniLiquid.checked = liquid;
        applyTester(enabled);
    }

    tester.addEventListener('change', () => {
        applyTester(tester.checked);
        testerWriter.save(tester.checked, testerMessage);
    });
    miniMode.addEventListener('change', () => {
        updateMiniMode();
        miniWriter.save(miniMode.value, miniMessage);
    });
    miniLiquid.addEventListener('change', () => {
        updateMiniMode();
        liquidWriter.save(miniLiquid.checked, miniLiquidMessage);
    });

    const dim = document.getElementById('wallpaper-dim');
    const blur = document.getElementById('wallpaper-blur');
    let signature = '', timer = null, posting = false;

    function appearance() {
        return JSON.stringify({
            theme: document.documentElement.dataset.theme || 'dark',
            accent: currentAccent,
            wallpaper: wallpaperFile,
            dim: Number(dim.value),
            blur: Number(blur.value),
            mini_background: window.miniBackgroundMode,
            visualizer_mode: document.getElementById('visualizer-mode').value,
        });
    }
    async function sendAppearance() {
        const value = appearance();
        if (posting || value === signature) return;
        posting = true;
        try {
            const response = await fetch('/api/appearance', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: value, signal: AbortSignal.timeout(3000),
            });
            if (response.ok) signature = value;
        } catch {
            // The next appearance change can retry a temporary connection failure.
        } finally {
            posting = false;
            if (value !== appearance()) publish();
        }
    }
    function publish() {
        clearTimeout(timer);
        timer = setTimeout(sendAppearance, 120);
    }
    for (const event of ['theme-changed', 'accent-changed']) document.addEventListener(event, publish);
    for (const event of ['wallpaper-media', 'wallpaper-rendered', 'wallpaper-effects-changed']) window.addEventListener(event, publish);
    window.publishAppearance = publish;
    window.addEventListener('visual-settings-loaded', event => load(event.detail));
    if (window.visualSettingsSnapshot) load(window.visualSettingsSnapshot);
    else publish();

    const output = document.getElementById('audio-output');
    const outputMessage = document.getElementById('audio-output-message');
    let devicesAvailable = false;
    window.addEventListener('visual-settings-loaded', () => { output.disabled = !devicesAvailable; });
    async function refreshOutputs() {
        try {
            const response = await fetch('/api/player/output-devices', { signal: AbortSignal.timeout(4000) });
            if (!response.ok) throw new Error('Не удалось прочитать аудиовыходы VLC.');
            const data = await response.json();
            output.replaceChildren(...(data.devices || []).map(device => new Option(device.name, device.id)));
            output.value = data.selected || '';
            devicesAvailable = Boolean(data.available);
            output.disabled = !settingsReady || !devicesAvailable;
            outputMessage.textContent = data.available ? '' : 'VLC не вернул отдельные устройства. Используется системный выход.';
        } catch (error) {
            outputMessage.textContent = error.message;
        }
    }
    document.getElementById('refresh-audio-output').addEventListener('click', refreshOutputs);
    output.addEventListener('change', async () => {
        output.disabled = true;
        try {
            const response = await fetch('/api/player/output-device', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device: output.value }), signal: AbortSignal.timeout(5000),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Не удалось применить аудиовыход.');
            await window.pywebview.api.save_audio_output(data.selected);
            outputMessage.textContent = '';
        } catch (error) {
            const warning = error.message;
            await refreshOutputs();
            outputMessage.textContent = warning;
        } finally {
            output.disabled = !settingsReady || !devicesAvailable;
        }
    });
    void refreshOutputs();
})();
