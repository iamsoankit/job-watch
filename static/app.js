(() => {
  'use strict';

  const publishedMode = document.querySelector('meta[name="job-watch-mode"]')?.content === 'published';
  const state = { jobs: [], sources: [], companies: [], settings: {}, searchLinks: [], lastScan: null, automation: {}, readOnly: publishedMode, scanning: false, loaded: false, busy: false };
  const $ = (selector, root = document) => root.querySelector(selector);
  const api = async (url, options = {}) => {
    const response = await fetch(url, { ...options, headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
    return data;
  };
  const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const safeURL = (value) => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } };
  const safeCareerURL = (value) => { try { const url = new URL(value); return url.protocol === 'https:' ? url.href : ''; } catch { return ''; } };
  const asDate = (value) => { if (!value) return null; const date = new Date(value); return Number.isNaN(date.getTime()) ? null : date; };
  const relativeDate = (value) => {
    const date = asDate(value); if (!date) return 'Date not provided';
    const days = Math.floor((Date.now() - date.getTime()) / 86400000);
    if (days <= 0) return 'Today'; if (days === 1) return 'Yesterday'; if (days < 7) return `${days} days ago`;
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: date.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined });
  };
  const listValue = (value) => Array.isArray(value) ? value : [];
  const activeJobs = () => state.jobs.filter((job) => job.active !== false && job.status !== 'archived');
  const visibleJobs = () => $('#job-scope').value === 'all' || $('#status-filter').value === 'archived' ? state.jobs : activeJobs();
  let toastTimer;
  function toast(message) { const element = $('#toast'); element.textContent = message; element.classList.add('visible'); clearTimeout(toastTimer); toastTimer = setTimeout(() => element.classList.remove('visible'), 3000); }
  function setConnection(online, label) { const element = $('#connection'); element.classList.toggle('is-online', online); element.classList.toggle('is-offline', !online); $('#connection-label').textContent = label; }
  function setSummary() {
    const scanDate = asDate(state.lastScan);
    const automation = state.automation || {};
    const nextScan = asDate(automation.next_scan);
    const label = state.readOnly
      ? `Auto-updated discovery view; track applications locally. ${automation.message || (asDate(automation.published_at) ? `Published ${asDate(automation.published_at).toLocaleString()}; refreshes daily.` : nextScan ? `Next refresh ${relativeDate(nextScan.toISOString()).toLowerCase()}.` : '')}`.trim()
      : automation.mode === 'local'
        ? `${automation.message || `Automatic discovery${automation.interval_minutes ? ` every ${automation.interval_minutes} minutes` : ' is scheduled'}`}${nextScan ? ` · next check ${nextScan.toLocaleString()}` : ''}`
        : state.scanning ? 'Your sources are being checked now.' : scanDate ? `Last checked ${relativeDate(scanDate.toISOString()).toLowerCase()}.` : 'Your search, ready when you are.';
    $('#scan-summary-text').textContent = label;
    const button = $('#scan-button'); button.disabled = state.readOnly || state.scanning || state.busy; button.hidden = state.readOnly; button.querySelector('span').textContent = state.scanning ? 'Scanning…' : 'Scan for jobs';
    $('#read-only-notice').hidden = !state.readOnly;
  }
  function renderMetrics() {
    const active = activeJobs();
    $('#metric-active').textContent = active.length;
    $('#metric-new').textContent = active.filter((job) => job.status === 'new').length;
    $('#metric-saved').textContent = active.filter((job) => job.status === 'saved').length;
    $('#metric-progress').textContent = active.filter((job) => ['applied', 'interview'].includes(job.status)).length;
  }
  function renderLocationFilter() {
    const select = $('#location-filter'); const selected = select.value;
    const locations = [...new Set(visibleJobs().map((job) => String(job.location || '').trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    select.innerHTML = '<option value="all">All locations</option>' + locations.map((location) => `<option value="${escapeHTML(location)}">${escapeHTML(location)}</option>`).join('');
    if (locations.includes(selected)) select.value = selected;
  }
  const statusName = (status) => ({ new: 'New', saved: 'Saved', applied: 'Applied', interview: 'Interview', rejected: 'Rejected', archived: 'Archived' }[status] || 'New');
  const icon = (name) => ({ pin: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 6.7c0 3.2-5 7.3-5 7.3S3 9.9 3 6.7a5 5 0 1 1 10 0Z"/><circle cx="8" cy="6.5" r="1.5"/></svg>', clock: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.3 1.5"/></svg>', bookmark: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h8v11l-4-2.7-4 2.7z"/></svg>', external: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M9 2.5h4.5V7M13.2 2.8 7.5 8.5"/><path d="M12 9v3.5a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1H7"/></svg>' }[name] || '');
  function renderJobs() {
    const list = $('#job-list'); const term = $('#search-input').value.trim().toLowerCase(); const status = $('#status-filter').value; const location = $('#location-filter').value; const sort = $('#sort-select').value;
    const candidates = status === 'archived' ? state.jobs : visibleJobs();
    let jobs = candidates.filter((job) => (status === 'all' || job.status === status) && (location === 'all' || String(job.location || '') === location));
    const ageLimit = $('#posted-age-filter').value;
    const includeUnknownDate = $('#include-unknown-date').checked;
    jobs = jobs.filter((job) => {
      const date = asDate(job.posted_at);
      if (!date) return includeUnknownDate;
      return ageLimit === 'all' || (Date.now() - date.getTime()) / 86400000 <= Number(ageLimit);
    });
    if (term) jobs = jobs.filter((job) => [job.title, job.source, job.location, job.description, ...(job.reasons || []), ...listValue(job.profile_matches), ...listValue(job.profile_gaps)].some((value) => String(value || '').toLowerCase().includes(term)));
    jobs.sort((a, b) => sort === 'score' ? (Number(b.score) || 0) - (Number(a.score) || 0) || (asDate(b.first_seen)?.getTime() || 0) - (asDate(a.first_seen)?.getTime() || 0) : sort === 'title' ? String(a.title || '').localeCompare(String(b.title || '')) : (asDate(b.first_seen)?.getTime() || asDate(b.posted_at)?.getTime() || 0) - (asDate(a.first_seen)?.getTime() || asDate(a.posted_at)?.getTime() || 0));
    $('#job-count').textContent = jobs.length;
    $('#results-note').textContent = jobs.length ? `Showing ${jobs.length} ${jobs.length === 1 ? 'opportunity' : 'opportunities'}${term || status !== 'all' || location !== 'all' ? ' matching your filters' : ''}.` : '';
    if (!jobs.length) {
      const noJobs = state.loaded && !activeJobs().length && $('#job-scope').value !== 'all';
      const hasNoRecentMatches = noJobs && Boolean(asDate(state.lastScan));
      const emptyCopy = hasNoRecentMatches
        ? 'The last scan found no recent matches for the current India/NCR, up to 2 years experience, and 14-day posting window. Adjust preferences or posting-date controls, or include unknown posting dates to broaden the search.'
        : state.readOnly ? 'The published discovery view refreshes automatically. Check the official company pages below for current openings.' : 'Run a scan to check your configured career sources. You can also tune your preferences or explore career pages below.';
      const emptyAction = noJobs && state.readOnly ? '' : noJobs ? '<button class="button button-primary" type="button" data-action="scan">Scan your sources</button>' : '<button class="action-button" type="button" data-action="clear-filters">Clear filters</button>';
      const heading = hasNoRecentMatches ? 'No recent matches found.' : noJobs ? 'Your search starts here.' : 'No roles match this view.';
      list.innerHTML = `<div class="empty-state"><span class="empty-illustration" aria-hidden="true">${noJobs ? '✳' : '⌕'}</span><h3>${heading}</h3><p>${noJobs ? emptyCopy : 'Try a different keyword, location, or status filter. You can also include archived and inactive roles to review your history.'}</p>${emptyAction}${noJobs && !hasNoRecentMatches ? '<p class="empty-help">No sample listings — only roles returned by your sources.</p>' : ''}</div>`;
      list.setAttribute('aria-busy', 'false'); return;
    }
    list.innerHTML = jobs.map((job) => {
      const id = escapeHTML(job.id); const link = safeURL(job.url); const score = Number(job.score); const exp = job.experience || {};
      const experience = exp.label || (exp.minimum !== null && exp.minimum !== undefined ? `${exp.minimum}+ yrs` : 'Experience not listed');
      const reasons = listValue(job.reasons).slice(0, 4).map((reason) => `<span class="reason-chip">${escapeHTML(reason)}</span>`).join('');
      const description = job.description ? `<p class="job-description">${escapeHTML(String(job.description).length > 360 ? `${String(job.description).slice(0, 360).trim()}…` : job.description)}</p>` : '';
      const postedDate = asDate(job.posted_at);
      const posted = postedDate ? `${postedDate.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })} · ${relativeDate(postedDate.toISOString())}` : 'Posting date unavailable';
      const dateBasis = ['published', 'posted_date'].includes(job.date_basis) ? ' · source posting date' : job.date_basis && job.date_basis !== 'unknown' ? ` · ${String(job.date_basis)}` : '';
      const discovered = job.first_seen ? new Date(job.first_seen).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Discovery time unavailable';
      const apply = link ? `<a class="action-button apply-action" href="${escapeHTML(link)}" target="_blank" rel="noopener noreferrer">View &amp; apply ${icon('external')}</a>` : '<span class="action-button" aria-disabled="true">Application link unavailable</span>';
      const matches = listValue(job.profile_matches).map((value) => `<span class="reason-chip match-chip">Match: ${escapeHTML(value)}</span>`).join('');
      const gaps = listValue(job.profile_gaps).map((value) => `<span class="reason-chip gap-chip">Check: ${escapeHTML(value)}</span>`).join('');
      const profileLabel = listValue(state.settings?.profile?.skills).length ? (matches ? 'Profile skills matched' : 'Review profile fit') : 'No skills supplied yet';
      const eligibility = job.eligibility === 'confirmed' ? 'Eligibility signals fit' : 'Review eligibility on employer page';
      const selectOptions = ['new', 'saved', 'applied', 'interview', 'rejected', 'archived'].map((value) => `<option value="${value}" ${job.status === value ? 'selected' : ''}>${statusName(value)}</option>`).join('');
      const trackActions = state.readOnly ? '' : `<button class="action-button ${job.status === 'saved' ? 'is-saved' : ''}" type="button" data-action="save" aria-pressed="${job.status === 'saved'}">${icon('bookmark')} ${job.status === 'saved' ? 'Saved' : 'Save'}</button>${job.status !== 'applied' ? '<button class="action-button" type="button" data-action="applied">Mark applied</button>' : ''}<button class="notes-toggle" type="button" data-action="notes" aria-expanded="false">${job.notes ? 'Edit note' : '+ Note'}</button><label class="sr-only" for="status-${id}">Update status for ${escapeHTML(job.title || 'role')}</label><select class="job-status" id="status-${id}" data-action="status">${selectOptions}</select>`;
      return `<article class="job-card" data-job-id="${id}"><div class="job-card-main"><div class="job-heading"><p class="company-name">${escapeHTML(job.source || 'Career source')}</p><h3 class="job-title">${escapeHTML(job.title || 'Untitled role')}</h3><div class="job-meta"><span>${icon('pin')} ${escapeHTML(job.location || 'Location not provided')}</span><span>${icon('clock')} Posted ${escapeHTML(posted)}${escapeHTML(dateBasis)}</span><span>Discovered ${escapeHTML(discovered)}</span><span>${escapeHTML(experience)}</span></div></div>${Number.isFinite(score) ? `<span class="job-score"><b>${Math.round(score)} / 100</b> priority</span>` : ''}</div>${reasons || matches || gaps ? `<div class="job-reasons">${reasons}${matches}${gaps}</div>` : ''}<p class="eligibility-note">${escapeHTML(eligibility)}${job.eligibility === 'review' ? ` · ${escapeHTML(listValue(job.reasons).join('; '))}` : ''}</p><p class="eligibility-note profile-note">${escapeHTML(profileLabel)}</p>${description}<div class="job-actions">${apply}${trackActions}</div><div class="notes-host"></div></article>`;
    }).join('');
    list.setAttribute('aria-busy', 'false');
  }
  function renderSources() {
    const list = $('#source-list');
    const checked = listValue(state.sources);
    const configured = listValue(state.settings?.sources);
    const byName = new Map(checked.map((source) => [source.name, source]));
    const sources = configured.length ? configured.map((source) => ({ ...source, ...(byName.get(source.name) || {}) })) : checked;
    $('#source-count').textContent = `${sources.length} ${sources.length === 1 ? 'source' : 'sources'}`;
    if (!sources.length) { list.innerHTML = '<p class="muted-copy">No sources configured yet. Add career boards in search preferences.</p>'; return; }
    list.innerHTML = sources.map((source) => {
      const sourceState = String(source.status || '').toLowerCase();
      const failed = ['error', 'failed', 'unhealthy'].includes(sourceState);
      const manual = sourceState === 'manual' || source.coverage === 'manual' || source.type === 'manual';
      const didCheck = Boolean(source.checked_at);
      const status = failed ? 'Needs attention' : manual ? 'Manual only' : didCheck ? 'Ready' : 'Not checked';
      const counts = didCheck ? `${Number(source.count) || 0} found · ${Number(source.matched) || 0} matched` : 'Raw results — · matched —';
      const manualReason = source.error || source.reason || 'Open the official career page; this source is not scanned automatically.';
      const detail = manual ? manualReason : source.error || (didCheck ? `Checked ${relativeDate(source.checked_at).toLowerCase()}` : counts);
      return `<div class="source-row"><span class="source-dot ${failed ? 'bad' : didCheck ? '' : 'wait'}" aria-hidden="true"></span><div class="source-info"><div class="source-name">${escapeHTML(source.name || source.type || 'Career source')}</div><div class="source-detail" title="${escapeHTML(manual ? manualReason : source.error || counts)}">${escapeHTML(detail)}</div>${didCheck ? `<div class="source-counts">${escapeHTML(counts)}</div>` : ''}</div><span class="source-status">${escapeHTML(status)}</span></div>`;
    }).join('');
  }
  function renderCompanies() {
    const list = $('#company-list');
    const query = $('#company-search').value.trim().toLowerCase();
    const companies = listValue(state.companies).filter((company) => !query || String(company.name || '').toLowerCase().includes(query));
    $('#company-count').textContent = companies.length;
    if (!companies.length) {
      list.innerHTML = `<p class="muted-copy">${state.companies.length ? 'No companies match your search.' : 'Company career-page coverage has not been provided by the discovery service.'}</p>`;
      return;
    }
    list.innerHTML = companies.map((company) => {
      const link = safeCareerURL(company.career_url);
      const status = company.coverage === 'manual' || company.status === 'manual' ? 'Manual link' : company.status === 'error' ? 'Scan needs attention' : company.status === 'ok' ? 'Automated coverage' : 'Not scanned';
      const postingDate = company.date_support === 'published' ? 'Posting dates supported' : company.date_support === 'mixed' ? 'Some posting dates available' : 'Posting dates not confirmed';
      const counts = company.checked_at ? `${Number(company.matched) || 0} matched · ${Number(company.count) || 0} checked` : '';
      const geography = company.country || 'India';
      return `<article class="company-row"><div class="company-row-main"><strong>${escapeHTML(company.name || 'Company')}</strong><span>${escapeHTML(geography)} · ${escapeHTML(status)}</span><small>${escapeHTML(postingDate)}${counts ? ` · ${escapeHTML(counts)}` : ''}</small></div>${link ? `<a class="action-button" href="${escapeHTML(link)}" target="_blank" rel="noopener noreferrer">Open career page ${icon('external')}</a>` : '<span class="source-status">Career link unavailable</span>'}</article>`;
    }).join('');
  }
  function renderLinks() {
    const defaults = [
      { name: 'Google Careers', url: 'https://www.google.com/about/careers/applications/jobs/results', description: 'Search roles and set your own location and experience filters.' },
      { name: 'LinkedIn Jobs', url: 'https://www.linkedin.com/jobs/', description: 'Use LinkedIn’s own job search and alerts.' },
      { name: 'Naukri', url: 'https://www.naukri.com/', description: 'Search jobs directly on Naukri.' },
      { name: 'Indeed India', url: 'https://in.indeed.com/', description: 'Search jobs directly on Indeed India.' }
    ];
    const links = [...defaults, ...listValue(state.searchLinks)].filter((item) => safeURL(item.url)); const container = $('#search-links');
    container.innerHTML = links.map((item) => `<a class="career-link" href="${escapeHTML(safeURL(item.url))}" target="_blank" rel="noopener noreferrer" title="${escapeHTML(item.description || item.name || '')}">${escapeHTML(item.name || 'Career site')} <span>↗</span></a>`).join('');
  }
  function renderSettings() {
    const settings = state.settings || {};
    const keywords = settings.include_title_keywords || settings.role_keywords || settings.keywords || [];
    $('#role-keywords').value = Array.isArray(keywords) ? keywords.join(', ') : String(keywords || '');
    $('#locations').value = Array.isArray(settings.locations) ? settings.locations.join(', ') : String(settings.locations || '');
    const maxExperience = settings.max_experience ?? settings.maximum_experience ?? 2;
    $('#max-experience').value = maxExperience;
    $('#profile-skills').value = listValue(settings.profile?.skills).join(', ');
    $('#max-age-days').value = settings.max_age_days ?? 14;
    $('#include-unknown-experience').checked = Boolean(settings.include_unknown_experience);
    $('#include-unknown-date-setting').checked = Boolean(settings.include_unknown_date);
    $('#posted-age-filter').value = ['7', '14'].includes(String(settings.max_age_days ?? 14)) ? String(settings.max_age_days ?? 14) : 'all';
    $('#include-unknown-date').checked = Boolean(settings.include_unknown_date);
    $('#sources-json').value = JSON.stringify(settings.sources || [], null, 2);
  }
  function render() {
    renderMetrics(); renderLocationFilter(); renderJobs(); renderSources(); renderCompanies(); renderLinks(); setSummary();
    if (state.readOnly) $('#settings-form').querySelectorAll('input, textarea, select, button, summary').forEach((element) => { element.disabled = true; });
  }
  async function loadState({ quiet = false } = {}) {
    try {
      const data = publishedMode ? await api('./state.json') : await api('/api/state');
      state.jobs = listValue(data.jobs); state.sources = listValue(data.sources); state.settings = data.settings && typeof data.settings === 'object' ? data.settings : {};
      state.companies = listValue(data.company_health || data.companies || data.sources).filter((company) => company.career_url || data.company_health || data.companies);
      state.searchLinks = listValue(data.search_links); state.lastScan = data.last_scan || null; state.automation = data.automation || {}; state.readOnly = publishedMode || Boolean(data.read_only); state.scanning = Boolean(data.scanning); state.loaded = true;
      setConnection(true, state.readOnly ? 'Auto-updated' : 'Connected'); render(); if (!loadState.settingsRendered) { renderSettings(); loadState.settingsRendered = true; }
      if (state.scanning) schedulePoll();
    } catch (error) {
      setConnection(false, 'API unavailable');
      if (!state.loaded) { state.loaded = true; $('#job-list').innerHTML = `<div class="empty-state"><span class="empty-illustration" aria-hidden="true">!</span><h3>Couldn’t reach your job search.</h3><p>${escapeHTML(error.message)} Check that the dashboard server is running, then try again.</p><button class="action-button" type="button" data-action="reload">Try again</button></div>`; $('#job-list').setAttribute('aria-busy', 'false'); $('#source-list').innerHTML = `<p class="muted-copy">Source status unavailable: ${escapeHTML(error.message)}</p>`; }
      if (!quiet) toast(`Could not refresh: ${error.message}`);
    }
  }
  let pollTimer;
  function schedulePoll() { clearTimeout(pollTimer); pollTimer = setTimeout(async () => { await loadState({ quiet: true }); if (state.scanning) schedulePoll(); }, 4000); }
  async function startScan() {
    if (state.readOnly || state.scanning || state.busy) return;
    state.scanning = true; render();
    try { await api('/api/scan', { method: 'POST' }); toast('Scan started. New results will appear here.'); schedulePoll(); }
    catch (error) { state.scanning = false; render(); toast(`Scan could not start: ${error.message}`); }
  }
  async function updateJob(job, patch) {
    if (state.readOnly) return;
    try {
      await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: 'PATCH', body: JSON.stringify(patch) });
      Object.assign(job, patch); render(); toast(patch.status ? `Status updated to ${statusName(patch.status)}.` : 'Note saved.');
    } catch (error) { toast(`Could not update role: ${error.message}`); }
  }
  function parseKeywords(value) { return value.split(/[\n,]+/).map((entry) => entry.trim()).filter(Boolean); }
  async function saveSettings(event) {
    event.preventDefault(); if (state.readOnly || state.busy) return;
    const feedback = $('#settings-feedback'); const saveButton = $('#settings-save'); let settings;
    try { settings = JSON.parse(JSON.stringify(state.settings || {})); }
    catch { settings = {}; }
    let sources;
    try { sources = JSON.parse($('#sources-json').value || '[]'); if (!Array.isArray(sources)) throw new Error('Sources must be a JSON array.'); }
    catch (error) { feedback.textContent = `Sources: ${error.message}`; feedback.classList.add('error'); $('#sources-json').focus(); return; }
    const maxExperience = $('#max-experience').value.trim();
    if (!maxExperience || !Number.isInteger(Number(maxExperience)) || Number(maxExperience) < 0 || Number(maxExperience) > 20) { feedback.textContent = 'Maximum required experience must be an integer from 0 to 20.'; feedback.classList.add('error'); $('#max-experience').focus(); return; }
    settings.include_title_keywords = parseKeywords($('#role-keywords').value);
    settings.locations = parseKeywords($('#locations').value);
    settings.max_experience = Number(maxExperience);
    const maxAgeDays = $('#max-age-days').value.trim();
    if (!maxAgeDays || !Number.isInteger(Number(maxAgeDays)) || Number(maxAgeDays) < 1 || Number(maxAgeDays) > 3650) { feedback.textContent = 'Maximum posting age must be an integer from 1 to 3650 days.'; feedback.classList.add('error'); $('#max-age-days').focus(); return; }
    settings.max_age_days = Number(maxAgeDays);
    settings.include_unknown_experience = $('#include-unknown-experience').checked;
    settings.include_unknown_date = $('#include-unknown-date-setting').checked;
    settings.country = 'India';
    settings.profile = { ...(settings.profile || {}), skills: parseKeywords($('#profile-skills').value) };
    settings.sources = sources;
    feedback.textContent = ''; feedback.classList.remove('error'); state.busy = true; saveButton.disabled = true; setSummary();
    try {
      const data = await api('/api/settings', { method: 'PUT', body: JSON.stringify(settings) });
      state.settings = data.settings && typeof data.settings === 'object' ? data.settings : settings;
      renderSettings(); feedback.textContent = 'Preferences saved.'; toast('Search preferences saved.');
    } catch (error) { feedback.textContent = error.message; feedback.classList.add('error'); }
    finally { state.busy = false; saveButton.disabled = false; setSummary(); }
  }
  function openNotes(card, job) {
    const host = $('.notes-host', card); const button = $('[data-action="notes"]', card); const expanded = button.getAttribute('aria-expanded') === 'true';
    if (expanded) { host.innerHTML = ''; button.setAttribute('aria-expanded', 'false'); return; }
    button.setAttribute('aria-expanded', 'true');
    host.innerHTML = `<form class="notes-editor"><label class="sr-only" for="note-${escapeHTML(job.id)}">Notes for ${escapeHTML(job.title || 'role')}</label><textarea id="note-${escapeHTML(job.id)}" maxlength="5000" placeholder="Interview notes, application details, or a reminder…">${escapeHTML(job.notes || '')}</textarea><button class="action-button" type="submit">Save note</button></form>`;
    $('textarea', host).focus();
  }
  function handleClick(event) {
    const button = event.target.closest('[data-action]'); if (!button) return;
    const action = button.dataset.action;
    if (action === 'scan') { startScan(); return; }
    if (action === 'clear-filters') { $('#search-input').value = ''; $('#status-filter').value = 'all'; $('#location-filter').value = 'all'; $('#job-scope').value = 'current'; renderJobs(); return; }
    if (action === 'reload') { loadState(); return; }
    const card = button.closest('[data-job-id]'); if (!card) return;
    const job = state.jobs.find((item) => String(item.id) === card.dataset.jobId); if (!job) return;
    if (action === 'save') updateJob(job, { status: job.status === 'saved' ? 'new' : 'saved' });
    if (action === 'applied') updateJob(job, { status: 'applied' });
    if (action === 'notes') openNotes(card, job);
  }
  function handleChange(event) {
    if (event.target.matches('#search-input, #status-filter, #location-filter, #sort-select, #posted-age-filter, #include-unknown-date')) { renderJobs(); return; }
    if (event.target.matches('.job-status')) { const job = state.jobs.find((item) => String(item.id) === event.target.closest('[data-job-id]').dataset.jobId); if (job) updateJob(job, { status: event.target.value }); }
  }
  function handleSubmit(event) {
    if (event.target.matches('#settings-form')) { saveSettings(event); return; }
    if (!event.target.matches('.notes-editor')) return;
    event.preventDefault(); const card = event.target.closest('[data-job-id]'); const job = state.jobs.find((item) => String(item.id) === card.dataset.jobId);
    if (job) updateJob(job, { notes: $('textarea', event.target).value });
  }
  $('#today-label').textContent = new Date().toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  $('#scan-button').addEventListener('click', startScan);
  $('#settings-form').addEventListener('submit', handleSubmit);
  $('#job-list').addEventListener('click', handleClick);
  $('#job-list').addEventListener('change', handleChange);
  $('#job-list').addEventListener('submit', handleSubmit);
  $('#search-input').addEventListener('input', renderJobs);
  $('#status-filter').addEventListener('change', () => { renderLocationFilter(); renderJobs(); });
  $('#location-filter').addEventListener('change', renderJobs);
  $('#job-scope').addEventListener('change', () => { renderLocationFilter(); renderJobs(); });
  $('#sort-select').addEventListener('change', renderJobs);
  $('#posted-age-filter').addEventListener('change', renderJobs);
  $('#include-unknown-date').addEventListener('change', renderJobs);
  $('#company-search').addEventListener('input', renderCompanies);
  if (publishedMode) {
    document.body.classList.add('published-mode');
    $('#scan-button').hidden = true;
    $('#read-only-notice').hidden = false;
    $('#settings-form').querySelectorAll('input, textarea, select, button, summary').forEach((element) => { element.disabled = true; });
  }
  loadState();
  setInterval(() => { if (!state.scanning && document.visibilityState === 'visible') loadState({ quiet: true }); }, 30000);
})();
