// ЭКВАЛАЙЗЕР: 1. Управление → 2. Частоты/значения → 3. Последовательный предпросмотр → 4. Сохранение.
(() => {
    const toggle = document.getElementById('eq-enabled');
    const preamp = document.getElementById('eq-preamp');
    const speed = document.getElementById('eq-speed');
    const preampValue = document.getElementById('eq-preamp-value');
    const bands = document.getElementById('eq-bands');
    const preset = document.getElementById('eq-preset');
    const save = document.getElementById('eq-save');
    const reset = document.getElementById('eq-reset');
    const retry = document.getElementById('eq-retry');
    const message = document.getElementById('eq-message');
    const createPreset = document.getElementById('eq-create-preset'), deletePreset = document.getElementById('eq-delete-preset'), presetName = document.getElementById('eq-preset-name');
    const panel = document.getElementById('eq-panel'), panelButton = document.getElementById('equalizer-button');
    let panelGeneration = 0, closingAnimation = null;
    // Немодальная панель: не сдвигает контент, Escape/кнопка возвращают фокус.
    function layoutPanel() {
        if (panel.hidden || document.documentElement.classList.contains('sidebar-resizing')) return;
        if (activeScreen === 'playing') {
            const cover = document.getElementById('large-track-cover').getBoundingClientRect();
            const footer = playerBar.getBoundingClientRect();
            panel.style.top = Math.min(footer.top - 64, cover.bottom + 8) + 'px';
            panel.style.setProperty('--eq-top', panel.style.top);
            panel.style.bottom = '0px';
            panel.classList.toggle('eq-compact', innerHeight - parseFloat(panel.style.top) < 310);
            panel.style.height = 'auto'; panel.style.maxHeight = 'none';
        } else {
            ['top','bottom','height','max-height'].forEach(name => panel.style.removeProperty(name));
        }
    }
    new ResizeObserver(layoutPanel).observe(document.getElementById('large-track-cover'));
    window.addEventListener('resize', layoutPanel);
    function openPanel() {
        if (playerBar.hidden) return;
        panelGeneration++; closingAnimation?.cancel(); panel.hidden = false;
        panelButton.setAttribute('aria-expanded','true'); document.documentElement.classList.add('eq-open'); layoutPanel(); panel.scrollTop = 0;
        (toggle.disabled ? document.getElementById('eq-close') : toggle).focus({preventScroll:true});
    }
    function closePanel(immediate = false) {
        if (panel.hidden || document.documentElement.classList.contains('sidebar-resizing')) return;
        const generation = ++panelGeneration, focusInside = panel.contains(document.activeElement);
        closingAnimation?.cancel();
        const finish = () => {
            if (generation !== panelGeneration) return;
            panel.hidden = true; document.documentElement.classList.remove('eq-open'); panelButton.setAttribute('aria-expanded','false');
            if (focusInside) (playerBar.hidden ? screenTitle : panelButton).focus({preventScroll:true});
        };
        if (immediate || matchMedia('(prefers-reduced-motion: reduce)').matches) { finish(); return; }
        closingAnimation = panel.animate([{opacity:1,transform:'translateY(0)'},{opacity:0,transform:'translateY(12px)'}],{duration:160,easing:'ease-in',fill:'forwards'});
        closingAnimation.finished.then(finish).catch(()=>{}).finally(()=>{if(generation===panelGeneration){closingAnimation?.cancel();closingAnimation=null;}});
    }
    panelButton.addEventListener('click', () => panel.hidden ? openPanel() : closePanel());
    document.getElementById('eq-close').addEventListener('click', () => closePanel());
    document.addEventListener('keydown', event => { if (event.key==='Escape'&&!panel.hidden) { event.preventDefault();closePanel(); } });
    window.equalizerPanel = {layout:layoutPanel,onPlayerVisibility(visible){if(!visible)closePanel(true);},open:openPanel,close:closePanel};
    new MutationObserver(() => message.classList.toggle('is-error',/не удалось|ошиб|недоступ|failed|could not|error|unavailable/i.test(window.i18n.source(message)))).observe(message,{childList:true,characterData:true,subtree:true});
    let ready = false, busy = false, saving = false, pending = null, timer = null;
    let revision = 0, saved = null, confirmed = null, presets = [], sliders = [];
    const db = value => (Number(value) > 0 ? '+' : '') + Number(value).toFixed(1) + ' дБ';
    const configuration = () => ({enabled: toggle.checked, preamp: Number(preamp.value), bands: sliders.map(slider => Number(slider.value)), speed_mode: speed.value});
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    function controls() {
        [toggle, preamp, speed, preset, reset, ...sliders].forEach(control => control.disabled = !ready || saving);
        save.disabled = !ready || saving || busy || pending !== null || timer !== null || !same(configuration(), confirmed) || same(confirmed, saved);
        retry.disabled = busy || saving;
        const idle = ready && !saving && !busy && pending === null && timer === null && same(configuration(), confirmed);
        createPreset.disabled = !idle || !presetName.value.trim();
        deletePreset.disabled = !idle || !presets.find(item => item.id === preset.value)?.custom;
        presetName.disabled = !ready || saving;
        panelButton.classList.toggle('is-enabled', Boolean(confirmed?.enabled));
        // Предупреждение не блокирует намеренный выбор усиления.
        const config = configuration();
        document.getElementById('eq-clipping').hidden = !config.enabled || config.preamp + Math.max(0, ...config.bands) <= 0;
    }
    function labels() {
        preampValue.textContent = db(preamp.value);
        sliders.forEach((slider, index) => document.getElementById('eq-value-' + index).textContent = db(slider.value));
        window.playerVisuals?.updateRanges();
    }
    function show(config) {
        toggle.checked = config.enabled; preamp.value = String(config.preamp); speed.value = config.speed_mode || 'standard';
        sliders.forEach((slider, i) => slider.value = String(config.bands[i]));
        const match = presets.find(item => item.preamp === config.preamp && same(item.bands, config.bands));
        preset.value = match?.id || 'custom'; labels(); controls();
    }
    // 2. Частоты не предполагаются наугад: получаем именно полосы установленного LibVLC.
    function build(frequencies) {
        bands.replaceChildren(); sliders = [];
        frequencies.forEach((frequency, index) => {
            const group = document.createElement('div'); group.className = 'eq-band';
            const label = document.createElement('label'); label.htmlFor = 'eq-band-' + index;
            label.textContent = frequency >= 1000 ? frequency / 1000 + ' кГц' : frequency + ' Гц';
            const slider = document.createElement('input'); slider.id = label.htmlFor;
            slider.type = 'range'; slider.min = '-20'; slider.max = '20'; slider.step = '.5'; slider.value = '0';
            slider.setAttribute('aria-label', 'Эквалайзер ' + label.textContent);
            slider.setAttribute('aria-orientation', 'vertical');
            const output = document.createElement('output'); output.id = 'eq-value-' + index; output.htmlFor = slider.id;
            slider.addEventListener('input', changed);
            group.append(label, slider, output); bands.append(group); sliders.push(slider);
        });
    }
    async function request(path, config, method) {
        const response = await fetch('/api/player/equalizer' + path, {
            method: method || (config ? 'POST' : 'GET'), cache: 'no-store', signal: AbortSignal.timeout(5000),
            ...(config ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config)} : {})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Ошибка сервера ' + response.status);
        return data;
    }
    async function initialize() {
        if (busy || saving) return;
        ready = false; busy = true; controls();
        message.textContent = 'Загружаем эквалайзер...';
        try {
            const data = await request('');
            if (!data.available || !data.writable) throw new Error(data.warning || 'Эквалайзер недоступен.');
            if (!Array.isArray(data.frequencies) || data.frequencies.length !== 10 || !data.current || !Array.isArray(data.current.bands) || data.current.bands.length !== 10) throw new Error('Некорректный ответ эквалайзера.');
            rebuildPresets(data.presets);
            build(data.frequencies); saved = {...data.saved, speed_mode:data.saved.speed_mode || 'standard'}; confirmed = {...data.current, speed_mode:data.current.speed_mode || 'standard'}; ready = true;
            show(confirmed); message.textContent = 'Изменения отправляются сразу. Задержка звука зависит от VLC.';
        } catch (error) { message.textContent = 'Не удалось загрузить эквалайзер: ' + error.message; }
        finally { busy = false; controls(); }
    }
    // 3. Не отправляем сотни параллельных запросов: короткое объединение и только последние значения.
    function changed(event) {
        if (!ready || saving) return;
        if (event?.target !== toggle) preset.value = 'custom';
        if (event?.target?.type === 'range') toggle.checked = true;
        labels(); pending = configuration(); revision++;
        clearTimeout(timer); timer = null;
        if (!busy) void flush();
        message.textContent = 'Применяем эквалайзер...'; controls();
    }
    async function flush() {
        if (busy || pending === null || !ready) return;
        busy = true; controls();
        const config = pending, sentRevision = revision; pending = null;
        try {
            const data = await request('/apply', config); confirmed = data.current;
            panel.dataset.commandApplyMs = String(data.command_apply_ms ?? '');
            window.playerMotion?.rate({standard:1,speedup:1.25,slowed:.8}[confirmed.speed_mode || 'standard']);
            if (sentRevision === revision) {
                message.textContent = same(confirmed, saved) ? 'Сохранённые значения применены.' : 'Настройки переданы VLC. Ещё не сохранены.';
                if (document.documentElement.dataset.developer === 'true' && Number.isFinite(data.command_apply_ms)) {
                    message.textContent += ` Команда: ${data.command_apply_ms} мс (не задержка звука).`;
                }
            }
        } catch (error) {
            if (sentRevision === revision) {
                // После отказа VLC показываем реально подтверждённые значения, не ложный успех.
                if (confirmed) show(confirmed);
                message.textContent = 'Не удалось применить эквалайзер: ' + error.message;
            }
        } finally {
            busy = false; controls();
            if (pending !== null && timer === null) void flush();
        }
    }
    toggle.addEventListener('change', changed); preamp.addEventListener('input', changed); speed.addEventListener('change', changed);
    preset.addEventListener('change', () => {
        const selected = presets.find(item => item.id === preset.value); if (!selected || !ready) return;
        if (selected.speed_mode) speed.value = selected.speed_mode;
        preamp.value = String(selected.preamp); sliders.forEach((slider, i) => slider.value = String(selected.bands[i]));
        toggle.checked = true; const id = selected.id; changed(); preset.value = id;
    });
    reset.addEventListener('click', () => {
        // Сброс значений не выключает эффект, пока пользователь сам не снимет переключатель.
        preamp.value = '0'; sliders.forEach(slider => slider.value = '0'); changed(); preset.value = 'flat';
    });
    // 4. Записываем только применённое состояние, защищённое сервером от устаревшего сохранения.
    save.addEventListener('click', async () => {
        if (save.disabled) return;
        saving = true; controls();
        try {
            const data = await request('/save', configuration()); saved = data.saved;
            message.textContent = 'Эквалайзер сохранён. Выбор останется после перезапуска.';
        } catch (error) { message.textContent = 'Звук изменён, но настройки не сохранены: ' + error.message; }
        finally { saving = false; controls(); }
    });
    // Свои пресеты — отдельные именованные копии усилений, не кнопка применения звука.
    function rebuildPresets(items, selected = preset.value) {
        presets = items || []; preset.replaceChildren(new Option('Ручной', 'custom'));
        presets.forEach(item => {const option=new Option(window.i18n.t((item.custom ? 'Мой: ' : '') + item.name),item.id); if(item.custom) option.dataset.userText='true'; preset.add(option);});
        preset.value = presets.some(item => item.id === selected) ? selected : 'custom';
    }
    presetName.addEventListener('input', controls);
    createPreset.addEventListener('click', async () => {
        if (createPreset.disabled) return;
        saving = true; controls();
        try {
            const data = await request('/presets',{name:presetName.value.trim(),config:configuration()});
            rebuildPresets(data.presets,data.created_id); presetName.value = ''; message.textContent = 'Свой пресет сохранён.';
        } catch (error) { message.textContent = 'Не удалось сохранить пресет: ' + error.message; }
        finally { saving = false; controls(); }
    });
    deletePreset.addEventListener('click', async () => {
        const selected = presets.find(item => item.id === preset.value);
        if (deletePreset.disabled || !selected?.custom || !confirm('Удалить пресет «'+selected.name+'»?')) return;
        saving = true; controls();
        try {const data=await request('/presets/'+encodeURIComponent(selected.id),null,'DELETE');rebuildPresets(data.presets);message.textContent='Свой пресет удалён. Текущее звучание не изменено.';}
        catch(error){message.textContent='Не удалось удалить пресет: '+error.message;}
        finally{saving=false;controls();}
    });
    document.addEventListener('language-changed', () => rebuildPresets(presets,preset.value));
    retry.addEventListener('click', initialize);
    void initialize();
})();
