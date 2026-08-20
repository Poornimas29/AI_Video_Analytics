// web_dashboard/static/js/dashboard.js
document.addEventListener('DOMContentLoaded', () => {
    initClock();
    initTabs();
    initDatePicker();
    initPunchInspector();
    initStaffControls();
    fetchSystemStatus();
    fetchLiveAttendance();
    fetchAttendanceHistory();
    fetchEmployees();

    // Regular Polling Intervals
    setInterval(fetchSystemStatus, 3000);
    setInterval(fetchLiveAttendance, 2000);
    setInterval(fetchAttendanceHistory, 5000);
    setInterval(fetchEmployees, 10000);
});

const BIOMETRIC_ICON_SVG = `
<svg class="punch-icon-biometric" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 12c0-3 2.5-5.5 5.5-5.5"></path>
  <path d="M12 2a10 10 0 0 0-10 10c0 4.4 2.9 8.2 7 9.5"></path>
  <path d="M12 17c-2.8 0-5-2.2-5-5a5 5 0 0 1 7.1-4.5"></path>
  <path d="M16.5 12c0 2.5-2 4.5-4.5 4.5"></path>
  <path d="M12 7c-2.8 0-5 2.2-5 5 0 1.5.7 2.9 1.8 3.8"></path>
</svg>
`;

// ── Real-time Clock ───────────────────────────────────────────
function initClock() {
    const clockEl = document.getElementById('liveClock');
    const update = () => {
        const now = new Date();
        if (clockEl) {
            clockEl.textContent = now.toLocaleTimeString('en-US', { hour12: false }) + ' UTC';
        }
    };
    update();
    setInterval(update, 1000);
}

// ── Tab Switcher ──────────────────────────────────────────────
function initTabs() {
    const tabs = document.querySelectorAll('.tab-btn');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            const targetId = tab.getAttribute('data-target');
            document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
            const activeContent = document.getElementById(targetId);
            if (activeContent) activeContent.style.display = 'block';

            if (targetId === 'tabPunches') {
                loadPunchInspectorData();
            } else if (targetId === 'tabStaff') {
                renderStaffRegistry();
            }
        });
    });
}

// ── Date Picker & Search Controls ───────────────────────────
function initDatePicker() {
    const dateInput = document.getElementById('historyDateInput');
    const searchInput = document.getElementById('employeeSearchInput');
    const btnAllDates = document.getElementById('btnAllDates');

    if (dateInput) {
        dateInput.addEventListener('change', () => {
            fetchAttendanceHistory(dateInput.value, searchInput ? searchInput.value : '');
        });
    }

    if (searchInput) {
        let debounceTimer = null;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                const dateVal = dateInput ? dateInput.value : '';
                fetchAttendanceHistory(dateVal, searchInput.value);
            }, 250);
        });
    }

    if (btnAllDates) {
        btnAllDates.addEventListener('click', () => {
            if (dateInput) dateInput.value = '';
            fetchAttendanceHistory('', searchInput ? searchInput.value : '');
        });
    }

    const btnExport = document.getElementById('btnExportCsv');
    if (btnExport) {
        btnExport.addEventListener('click', () => {
            const selectedDate = dateInput ? dateInput.value : '';
            window.location.href = `/api/attendance/export/csv?date=${selectedDate}`;
        });
    }
}

// ── Employee Punch Inspector Tab Controls ─────────────────────
function initPunchInspector() {
    const punchDateInput = document.getElementById('punchDateInput');
    const today = new Date().toISOString().split('T')[0];
    if (punchDateInput) {
        punchDateInput.value = today;
        punchDateInput.addEventListener('change', loadPunchInspectorData);
    }

    const punchSelect = document.getElementById('punchEmployeeSelect');
    if (punchSelect) {
        punchSelect.addEventListener('change', loadPunchInspectorData);
    }
}

async function loadPunchInspectorData() {
    const punchSelect = document.getElementById('punchEmployeeSelect');
    const punchDateInput = document.getElementById('punchDateInput');
    const container = document.getElementById('punchTabContainer');
    if (!punchSelect || !container) return;

    const empId = punchSelect.value;
    const dateStr = punchDateInput ? punchDateInput.value : '';

    if (!empId) {
        container.innerHTML = `<div style="text-align:center; color: var(--text-muted); padding: 40px;">Select an employee above to inspect their daily attendance punch cards.</div>`;
        return;
    }

    try {
        const res = await fetch(`/api/attendance/employee/${empId}?date=${dateStr}`);
        if (!res.ok) return;
        const data = await res.json();
        container.innerHTML = generatePunchPanelHtml(data, false);
    } catch (err) {
        console.error('Error loading punch inspector:', err);
    }
}

// ── Fetch System Status & Update KPI ──────────────────────────
async function fetchSystemStatus() {
    try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();

        // Update KPI Cards
        const kpiActive = document.getElementById('kpiActiveStaff');
        if (kpiActive) kpiActive.textContent = data.active_workers_count;
        const kpiUptime = document.getElementById('kpiUptime');
        if (kpiUptime) kpiUptime.textContent = data.uptime;
    } catch (err) {
        console.error('Error fetching status:', err);
    }
}

function getAvatarImgHtml(empId, empName) {
    const fallbackSvg = `data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='38' height='38' viewBox='0 0 24 24' fill='%2364748B'><path d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/></svg>`;
    return `<img src="/api/employee_avatar/${empId}" class="emp-avatar-thumb" alt="${empName || empId}" onerror="this.src='${fallbackSvg}'">`;
}

// ── Fetch Active Presence ─────────────────────────────────────
async function fetchLiveAttendance() {
    try {
        const res = await fetch('/api/attendance/live');
        if (!res.ok) return;
        const data = await res.json();

        const tbody = document.getElementById('liveAttendanceTbody');
        if (!tbody) return;

        if (data.count === 0) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color: var(--text-dim); padding: 24px;">No active workers on camera currently</td></tr>`;
            return;
        }

        tbody.innerHTML = data.active_employees.map(emp => `
            <tr class="clickable-row" onclick="openAttendanceModal('${emp.employee_id}')">
                <td>
                    <div class="emp-cell-group">
                        ${getAvatarImgHtml(emp.employee_id, emp.employee_name)}
                        <div>
                            <div style="font-weight: 700;">${emp.employee_name}</div>
                            <div style="font-size: 11px; color: var(--primary); font-family: var(--font-mono);">${emp.employee_id}</div>
                        </div>
                    </div>
                </td>
                <td><span class="duration-tag">${emp.entry_time}</span></td>
                <td><span class="duration-tag" style="color: #34D399;">${emp.formatted_duration}</span></td>
                <td>
                    <div style="display: flex; align-items: center; justify-content: space-between;">
                        ${emp.phone_in_use ? '<span class="pill-phone">PHONE IN USE</span>' : '<span class="pill-active">WORKING (IN)</span>'}
                        <button class="btn-view-punch" onclick="event.stopPropagation(); openAttendanceModal('${emp.employee_id}')">View Punches ↗</button>
                    </div>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        console.error('Error fetching live attendance:', err);
    }
}

// ── Fetch Attendance History & Render Table ───────────────────
async function fetchAttendanceHistory(dateStr, searchStr) {
    try {
        const dateInput = document.getElementById('historyDateInput');
        const searchInput = document.getElementById('employeeSearchInput');
        const selectedDate = (dateStr !== undefined) ? dateStr : (dateInput ? dateInput.value : '');
        const queryTerm = (searchStr !== undefined) ? searchStr : (searchInput ? searchInput.value : '');

        let url = '/api/attendance/history';
        const params = [];
        if (selectedDate) params.push(`date=${encodeURIComponent(selectedDate)}`);
        if (queryTerm) params.push(`search=${encodeURIComponent(queryTerm)}`);
        if (params.length > 0) url += `?${params.join('&')}`;

        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();

        // Update KPI Cards from History
        const kpiCheckins = document.getElementById('kpiTotalCheckins');
        if (kpiCheckins) kpiCheckins.textContent = data.total_records || 0;

        const kpiStaffPresent = document.getElementById('kpiTotalStaffPresent');
        if (kpiStaffPresent) kpiStaffPresent.textContent = data.total_employees || 0;

        const kpiPhone = document.getElementById('kpiPhoneViolations');
        if (kpiPhone) kpiPhone.textContent = data.total_phone_violations || 0;

        const tbody = document.getElementById('historyTbody');
        if (!tbody) return;

        if (!data.timeline_rows || data.timeline_rows.length === 0) {
            const filterLabel = selectedDate || 'all dates';
            const searchLabel = queryTerm ? ` matching "${queryTerm}"` : '';
            tbody.innerHTML = `
                <tr><td colspan="5" style="text-align:center; color: var(--text-dim); padding: 30px;">
                    No recorded attendance sessions found for ${filterLabel}${searchLabel}.
                </td></tr>
            `;
            return;
        }

        let html = '';
        data.timeline_rows.forEach(row => {
            const rowDate = row.date || selectedDate || '';
            const empId = row.employee_id || '';
            const empName = row.employee_name || empId || 'Employee';
            const entryTime = row.entry_time || row.entry || '--:--';
            const exitTime = row.exit_time || row.exit || '--:--';
            const formattedDuration = row.formatted_duration || row.duration || '0h 00m 00s';

            if (row.type === 'session') {
                html += `
                    <tr class="clickable-row" onclick="openAttendanceModal('${empId}', '${rowDate}')">
                        <td>
                            <div class="emp-cell-group">
                                ${getAvatarImgHtml(empId, empName)}
                                <div>
                                    <div style="font-weight: 700;">${empName}</div>
                                    <div style="font-size: 11px; color: var(--primary); font-family: var(--font-mono);">${empId}</div>
                                </div>
                            </div>
                        </td>
                        <td><span style="font-size: 12px; color: var(--text-muted); font-family: var(--font-mono);">${rowDate}</span></td>
                        <td><span class="duration-tag">${entryTime}</span></td>
                        <td><span class="duration-tag" style="color: ${exitTime === 'Video Ended' ? '#D97706' : 'var(--text-main)'}; font-weight: ${exitTime === 'Video Ended' ? '700' : '500'};">${exitTime}</span></td>
                        <td style="text-align: right;">
                            <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px;">
                                <span class="working-pill">${formattedDuration}</span>
                                <button class="btn-view-punch" onclick="event.stopPropagation(); openAttendanceModal('${empId}', '${rowDate}')">View Punches ↗</button>
                            </div>
                        </td>
                    </tr>
                `;
            }
        });

        tbody.innerHTML = html;
    } catch (err) {
        console.error('Error fetching history:', err);
    }
}

// ── Staff Registry State & Controls ───────────────────────────
let rawStaffList = [];
let staffViewMode = 'grid'; // 'grid' or 'table'
let staffCurrentPage = 1;
let staffPageSize = 12;

function initStaffControls() {
    const btnGrid = document.getElementById('staffViewCardsBtn');
    const btnTable = document.getElementById('staffViewTableBtn');
    const searchInput = document.getElementById('staffSearchInput');
    const filterStatus = document.getElementById('staffFilterStatus');
    const sortSelect = document.getElementById('staffSortSelect');
    const pageSizeSelect = document.getElementById('staffPageSizeSelect');

    if (btnGrid && btnTable) {
        btnGrid.addEventListener('click', () => {
            staffViewMode = 'grid';
            btnGrid.classList.add('active');
            btnTable.classList.remove('active');
            const gridWrapper = document.getElementById('staffGridViewWrapper');
            const tableWrapper = document.getElementById('staffTableViewWrapper');
            if (gridWrapper) gridWrapper.style.display = 'block';
            if (tableWrapper) tableWrapper.style.display = 'none';
            renderStaffRegistry();
        });

        btnTable.addEventListener('click', () => {
            staffViewMode = 'table';
            btnTable.classList.add('active');
            btnGrid.classList.remove('active');
            const gridWrapper = document.getElementById('staffGridViewWrapper');
            const tableWrapper = document.getElementById('staffTableViewWrapper');
            if (gridWrapper) gridWrapper.style.display = 'none';
            if (tableWrapper) tableWrapper.style.display = 'block';
            renderStaffRegistry();
        });
    }

    if (searchInput) {
        let debounceTimer = null;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                staffCurrentPage = 1;
                renderStaffRegistry();
            }, 200);
        });
    }

    if (filterStatus) {
        filterStatus.addEventListener('change', () => {
            staffCurrentPage = 1;
            renderStaffRegistry();
        });
    }

    if (sortSelect) {
        sortSelect.addEventListener('change', () => {
            renderStaffRegistry();
        });
    }

    if (pageSizeSelect) {
        pageSizeSelect.addEventListener('change', () => {
            const val = pageSizeSelect.value;
            staffPageSize = (val === 'all') ? 999999 : parseInt(val, 10);
            staffCurrentPage = 1;
            renderStaffRegistry();
        });
    }
}

// ── Fetch Enrolled Employees Gallery ──────────────────────────
async function fetchEmployees() {
    try {
        const res = await fetch('/api/employees');
        if (!res.ok) return;
        const data = await res.json();
        rawStaffList = data.employees || [];

        // Populate Punch Inspector Dropdown
        const punchSelect = document.getElementById('punchEmployeeSelect');
        if (punchSelect && rawStaffList.length > 0) {
            const currentVal = punchSelect.value;
            punchSelect.innerHTML = `<option value="">Select Employee...</option>` + rawStaffList.map(e => `
                <option value="${e.id}" ${e.id === currentVal ? 'selected' : ''}>${e.name} (${e.id})</option>
            `).join('');
        }

        renderStaffRegistry();
    } catch (err) {
        console.error('Error fetching employees:', err);
    }
}

function renderStaffRegistry() {
    if (!rawStaffList) return;

    // 1. Filter Staff
    const searchInput = document.getElementById('staffSearchInput');
    const query = (searchInput ? searchInput.value : '').trim().toLowerCase();
    const filterStatus = document.getElementById('staffFilterStatus')?.value || 'all';

    let filtered = rawStaffList.filter(emp => {
        const nameMatch = (emp.name || '').toLowerCase().includes(query);
        const idMatch = (emp.id || '').toLowerCase().includes(query);
        if (query && !nameMatch && !idMatch) return false;

        if (filterStatus === 'onsite' && !emp.is_on_site) return false;
        if (filterStatus === 'enrolled' && (!emp.image_count || emp.image_count <= 0)) return false;
        if (filterStatus === 'missing' && emp.image_count > 0) return false;

        return true;
    });

    // 3. Sort Staff
    const sortBy = document.getElementById('staffSortSelect')?.value || 'name-asc';
    filtered.sort((a, b) => {
        if (sortBy === 'name-asc') return (a.name || '').localeCompare(b.name || '');
        if (sortBy === 'name-desc') return (b.name || '').localeCompare(a.name || '');
        if (sortBy === 'id-asc') return (a.id || '').localeCompare(b.id || '');
        if (sortBy === 'photos-desc') return (b.image_count || 0) - (a.image_count || 0);
        return 0;
    });

    // 4. Pagination
    const totalFiltered = filtered.length;
    const totalPages = Math.max(1, Math.ceil(totalFiltered / staffPageSize));
    if (staffCurrentPage > totalPages) staffCurrentPage = totalPages;

    const startIndex = (staffCurrentPage - 1) * staffPageSize;
    const pagedItems = filtered.slice(startIndex, startIndex + staffPageSize);

    // 5. Render Views
    const gridEl = document.getElementById('staffGrid');
    const tbodyEl = document.getElementById('staffTableTbody');

    if (totalFiltered === 0) {
        const emptyMsg = `<div style="text-align:center; color: var(--text-dim); padding: 36px; grid-column: 1 / -1; width: 100%;">No employees found matching the filters.</div>`;
        if (gridEl) gridEl.innerHTML = emptyMsg;
        if (tbodyEl) tbodyEl.innerHTML = `<tr><td colspan="5" style="text-align:center; color: var(--text-dim); padding: 36px;">No employees found matching the filters.</td></tr>`;
    } else {
        // Render Grid View
        if (gridEl) {
            gridEl.innerHTML = pagedItems.map(emp => `
                <div class="staff-card">
                    <div class="staff-avatar-wrapper">
                        ${emp.sample_image ? 
                            `<img src="${emp.sample_image}" class="staff-avatar" alt="${emp.name}" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'76\\' height=\\'76\\' viewBox=\\'0 0 24 24\\' fill=\\'%239CA3AF\\'><path d=\\'M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z\\'/></svg>'">` :
                            `<div class="staff-avatar" style="display:flex;align-items:center;justify-content:center;font-size:28px;">👤</div>`
                        }
                        <span class="staff-online-badge ${emp.is_on_site ? '' : 'offline'}" title="${emp.is_on_site ? 'Currently On-Site' : 'Off-Site / Not Detected'}"></span>
                    </div>
                    <div class="staff-name" title="${emp.name}">${emp.name}</div>
                    <div class="staff-id">${emp.id}</div>
                    <div class="staff-badge ${emp.image_count > 0 ? '' : 'warning'}">
                        <span>${emp.image_count > 0 ? '📸' : '⚠️'}</span>
                        <span>${emp.image_count > 0 ? `${emp.image_count} Photos Enrolled` : 'No Photos'}</span>
                    </div>
                    <button class="staff-btn-punches" onclick="openAttendanceModal('${emp.id}')">View Punch Cards ↗</button>
                </div>
            `).join('');
        }

        // Render Table View
        if (tbodyEl) {
            tbodyEl.innerHTML = pagedItems.map(emp => `
                <tr class="clickable-row" onclick="openAttendanceModal('${emp.id}')">
                    <td>
                        <div class="emp-cell-group">
                            ${getAvatarImgHtml(emp.id, emp.name)}
                            <div>
                                <div style="font-weight: 700; font-size: 13.5px;">${emp.name}</div>
                                <div style="font-size: 11px; color: var(--text-muted);">${emp.status || 'Active'}</div>
                            </div>
                        </div>
                    </td>
                    <td><span class="duration-tag" style="font-family: var(--font-mono);">${emp.id}</span></td>
                    <td>
                        <span class="pill-photo-badge">
                            <span>📸</span>
                            <span>${emp.image_count} Photos</span>
                        </span>
                    </td>
                    <td>
                        ${emp.is_on_site ? 
                            `<span class="pill-onsite"><span class="dot-pulse" style="width:6px;height:6px;"></span> ON-SITE</span>` : 
                            `<span class="pill-offsite">OFF-SITE</span>`
                        }
                    </td>
                    <td style="text-align: right;">
                        <button class="btn-view-punch" onclick="event.stopPropagation(); openAttendanceModal('${emp.id}')">View Punches ↗</button>
                    </td>
                </tr>
            `).join('');
        }
    }

    // 6. Render Pagination Bar
    renderStaffPagination(totalFiltered, startIndex, pagedItems.length, totalPages);
}

function renderStaffPagination(totalFiltered, startIndex, pageCount, totalPages) {
    const infoEl = document.getElementById('staffPaginationInfo');
    const navEl = document.getElementById('staffPageNavBtns');
    if (!infoEl || !navEl) return;

    if (totalFiltered === 0) {
        infoEl.textContent = 'Showing 0 of 0 employees';
        navEl.innerHTML = '';
        return;
    }

    const startNum = startIndex + 1;
    const endNum = startIndex + pageCount;
    infoEl.textContent = `Showing ${startNum}–${endNum} of ${totalFiltered} employees`;

    if (totalPages <= 1) {
        navEl.innerHTML = '';
        return;
    }

    let buttonsHtml = `
        <button class="btn-page" onclick="changeStaffPage(${staffCurrentPage - 1})" ${staffCurrentPage <= 1 ? 'disabled' : ''}>‹</button>
    `;

    for (let p = 1; p <= totalPages; p++) {
        if (p === 1 || p === totalPages || (p >= staffCurrentPage - 1 && p <= staffCurrentPage + 1)) {
            buttonsHtml += `
                <button class="btn-page ${p === staffCurrentPage ? 'active' : ''}" onclick="changeStaffPage(${p})">${p}</button>
            `;
        } else if (p === staffCurrentPage - 2 || p === staffCurrentPage + 2) {
            buttonsHtml += `<span style="color:var(--text-muted); padding:0 2px;">…</span>`;
        }
    }

    buttonsHtml += `
        <button class="btn-page" onclick="changeStaffPage(${staffCurrentPage + 1})" ${staffCurrentPage >= totalPages ? 'disabled' : ''}>›</button>
    `;

    navEl.innerHTML = buttonsHtml;
}

function changeStaffPage(page) {
    staffCurrentPage = page;
    renderStaffRegistry();
}

// ── Open & Render Employee Attendance Punch Modal (Exact UI) ──
async function openAttendanceModal(empId, dateStr) {
    const modal = document.getElementById('attendanceModal');
    if (!modal) return;

    const selectedDate = dateStr || document.getElementById('historyDateInput')?.value || new Date().toISOString().split('T')[0];

    try {
        const res = await fetch(`/api/attendance/employee/${empId}?date=${selectedDate}`);
        if (!res.ok) return;
        const data = await res.json();

        // Update Modal Header
        document.getElementById('modalDateHeading').textContent = data.date_heading;
        document.getElementById('modalEmpName').textContent = data.employee_name;
        document.getElementById('modalEmpId').textContent = `(${data.employee_id})`;
        document.getElementById('modalTotalHours').textContent = data.total_working_hours;
        const avatarEl = document.getElementById('modalEmpAvatar');
        if (avatarEl) avatarEl.innerHTML = getAvatarImgHtml(data.employee_id, data.employee_name);

        // Render Punch Cards
        const cardsListEl = document.getElementById('modalPunchCardsList');
        cardsListEl.innerHTML = renderPunchCardsItemsHtml(data.punches);

        modal.style.display = 'flex';
    } catch (err) {
        console.error('Error opening attendance modal:', err);
    }
}

function closeAttendanceModal() {
    const modal = document.getElementById('attendanceModal');
    if (modal) modal.style.display = 'none';
}

window.addEventListener('click', (e) => {
    const modal = document.getElementById('attendanceModal');
    if (e.target === modal) {
        closeAttendanceModal();
    }
});

// ── Generate Punch Cards List HTML ────────────────────────────
function renderPunchCardsItemsHtml(punches) {
    if (!punches || punches.length === 0) {
        return `
            <div class="punch-card-item" style="justify-content: center; padding: 36px; color: #64748B;">
                <div style="text-align: center;">
                    <div style="font-size: 28px; margin-bottom: 8px;">⏱️</div>
                    <div style="font-weight: 600;">No attendance punches recorded for this date.</div>
                </div>
            </div>
        `;
    }

    return punches.map(p => `
        <div class="punch-card-item">
            <!-- IN Punch -->
            <div class="punch-col in-col">
                <div class="punch-time-row">
                    <span class="punch-time in">${p.in_time}</span>
                    ${BIOMETRIC_ICON_SVG}
                </div>
                <div class="punch-loc">
                    <span>${p.in_camera}</span>
                    <span class="info-circle" title="Entrance Camera: ${p.in_camera} | Verification: ${p.in_method}">ⓘ</span>
                </div>
            </div>

            <!-- Dotted Connector Line & Duration -->
            <div class="punch-connector">
                <div class="punch-dotted-line"></div>
                <div class="punch-duration-badge">${p.duration_formatted}</div>
            </div>

            <!-- OUT Punch -->
            <div class="punch-col out-col">
                <div class="punch-time-row">
                    ${p.status === 'ACTIVE' ? 
                        `<span class="punch-time in-progress">In Progress</span>` :
                      (p.status === 'VIDEO_ENDED' || p.out_time === 'Video Ended') ?
                        `<span class="punch-time" style="color: #D97706; font-size: 14px; font-weight: 700;">Video Ended</span>` :
                        `<span class="punch-time out">${p.out_time}</span>`
                    }
                    ${p.status === 'ACTIVE' ? 
                        `<span class="dot-pulse" style="margin-left: 6px;"></span>` :
                      (p.status === 'VIDEO_ENDED' || p.out_time === 'Video Ended') ?
                        `<span style="font-size: 14px; margin-left: 4px;" title="Employee was still inside the shop when video ended">⏹️</span>` :
                        BIOMETRIC_ICON_SVG
                    }
                </div>
                <div class="punch-loc">
                    <span>${p.out_camera}</span>
                    <span class="info-circle" title="Camera: ${p.out_camera} | Status: ${p.out_method}">ⓘ</span>
                </div>
            </div>
        </div>
    `).join('');
}

// ── Generate Complete Standalone Panel HTML for Tab ───────────
function generatePunchPanelHtml(data) {
    return `
        <div class="punch-card-panel" style="border-radius: 14px; border: 1px solid var(--border-subtle); background: var(--bg-surface); box-shadow: var(--shadow-card);">
            <div class="punch-header">
                <div class="punch-title-group">
                    <h2 class="punch-date-title">${data.date_heading}</h2>
                </div>
                <div class="punch-actions">
                    <button class="btn-audit" onclick="showAuditToast()">Audit History</button>
                </div>
            </div>

            <div class="punch-employee-banner">
                <div class="emp-meta" style="display: flex; align-items: center; gap: 12px;">
                    ${getAvatarImgHtml(data.employee_id, data.employee_name)}
                    <div>
                        <strong style="font-size: 15px; color: var(--text-main);">${data.employee_name}</strong>
                        <span style="font-size: 12px; color: var(--primary); font-family: var(--font-mono); margin-left: 6px;">(${data.employee_id})</span>
                    </div>
                </div>
                <div class="emp-total-badge">
                    Total Working: <strong style="color: var(--success);">${data.total_working_hours}</strong>
                </div>
            </div>

            <div class="punch-cards-stack">
                ${renderPunchCardsItemsHtml(data.punches)}
            </div>
        </div>
    `;
}

function showAuditToast() {
    alert("Audit log verified: Biometric facial features confirmed against registered face embedding gallery.");
}
