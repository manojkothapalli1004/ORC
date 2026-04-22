const sections = [
    { id: 'overview', label: 'System overview' },
    { id: 'workflow', label: 'Workflow state' },
    { id: 'queue', label: 'Dispatch queue' },
    { id: 'sessions', label: 'Sessions' },
    { id: 'ideas', label: 'Ideas' },
    { id: 'assistant', label: 'Assistant Brain' },
    { id: 'handoff', label: 'Handoff' },
    { id: 'proposals', label: 'Proposals' },
    { id: 'approvals', label: 'Approvals' },
    { id: 'experiments', label: 'Experiments' },
    { id: 'providers', label: 'Providers / roles' },
];

const mock = {
    summary: {
        liveWorkflows: 3,
        openProposals: 5,
        pendingApprovals: 2,
        experimentWinRate: '84%',
        tokensToday: '148k',
        approvalRate: '92%',
    },
    workflows: [
        {
            id: 'wf-control-142',
            status: 'building',
            approval_mode: 'auto_with_limits',
            priority: 'High',
            updated_at: '2m ago',
            owner: 'Builder · Anthropic',
            summary: 'Routing shell refactor moving through builder pass with bounded file scope.',
            providers: [
                { role: 'reviewer', provider: 'openai', model: 'gpt-4o' },
                { role: 'planner', provider: 'openai', model: 'gpt-4o' },
                { role: 'builder', provider: 'anthropic', model: 'claude-sonnet-4-20250514' },
            ],
            proposals: [
                {
                    id: 'prop-201',
                    batch_index: 12,
                    prompt: 'Elevate information hierarchy and reduce visual noise across operator dashboard sections.',
                    response: 'UI shell refined with cleaner hierarchy and safer presentation-only changes.',
                    files_affected: ['ui/index.html', 'ui/styles.css', 'ui/app.js'],
                    token_count: 18200,
                    approval: 'auto_approved',
                },
                {
                    id: 'prop-202',
                    batch_index: 13,
                    prompt: 'Add compact proposal scan view with file impact and approval visibility.',
                    response: 'Proposal summary view added with read-only placeholders.',
                    files_affected: ['ui/app.js', 'ui/styles.css'],
                    token_count: 11400,
                    approval: 'pending',
                },
            ],
            summary_obj: {
                workflow_id: 'wf-control-142',
                title: 'Operator shell v1',
                total_batches: 6,
                completed_batches: 4,
                total_tokens: 51000,
                files_changed: ['ui/index.html', 'ui/styles.css', 'ui/app.js'],
                outcome: 'in_progress',
            },
            demo_events: [],
            current_stage: 'building',
            is_demo: false,
        },
        {
            id: 'wf-exp-087',
            status: 'awaiting_approval',
            approval_mode: 'human',
            priority: 'Medium',
            updated_at: '9m ago',
            owner: 'Reviewer · OpenAI',
            summary: 'Proposal batch paused for operator sign-off on experiment cutoff parameters.',
            providers: [
                { role: 'reviewer', provider: 'openai', model: 'gpt-4o' },
                { role: 'planner', provider: 'openai', model: 'gpt-4o' },
                { role: 'builder', provider: 'anthropic', model: 'claude-sonnet-4-20250514' },
            ],
            proposals: [
                {
                    id: 'prop-203',
                    batch_index: 14,
                    prompt: 'Create executive experiment cards with progression, outcome, and operator-readable deltas.',
                    response: 'Executive experiment summaries prepared for review.',
                    files_affected: ['ui/app.js'],
                    token_count: 8100,
                    approval: 'pending',
                },
            ],
            summary_obj: {
                workflow_id: 'wf-exp-087',
                title: 'Approval framing study',
                total_batches: 6,
                completed_batches: 5,
                total_tokens: 39000,
                files_changed: ['ui/app.js', 'ui/styles.css', 'ui/index.html'],
                outcome: 'in_progress',
            },
            demo_events: [],
            current_stage: 'awaiting_approval',
            is_demo: false,
        },
    ],
    operators: [
        { name: 'Control tower', state: 'steady', detail: 'No dangerous controls exposed in preview shell.' },
        { name: 'Approval queue', state: 'active', detail: 'Read-only approval summaries remain visible without actions.' },
        { name: 'Fallback layer', state: 'loaded', detail: 'Premium shell remains previewable even when backend is unavailable.' },
    ],
};

const state = {
    activeSection: 'overview',
    apiMode: 'loading',
    workflows: [],
    queue: null,
    sessions: null,
    sessionDetails: {},
    ideas: null,
    health: null,
    config: null,
    error: null,
    selectedWorkflowId: null,
    selectedSessionId: null,
    selectedIdeaId: null,
    selectedWorkflowMode: 'normal',
    approvalActionKey: '',
    dispatchActionKey: '',
    lastDispatchedJobId: null,
    assignActionKey: '',
    promptPreview: null,
    promptPreviewSessionId: null,
    promptPreviewLoading: '',
    deliveryActionKey: '',
    resultFormSessionId: null,
    resultActionKey: '',
    resultDraft: {
        outcome: 'success',
        summary: '',
        notes: '',
        nextAction: 'Await next assignment.',
        artifactRef: '',
    },
};

function badgeClass(value) {
    return `status-badge status-${String(value).replace(/\s+/g, '_').toLowerCase()}`;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function formatRelative(value) {
    if (!value) return 'Unknown';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const diffMs = Date.now() - date.getTime();
    const diffMin = Math.max(0, Math.round(diffMs / 60000));
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return `${Math.round(diffHr / 24)}d ago`;
}

function titleCase(value) {
    return String(value || '')
        .replaceAll('_', ' ')
        .replace(/\b\w/g, char => char.toUpperCase());
}

function card(title, body, actions = '') {
    return `
        <section class="card">
            <div class="card-header">
                <div><div class="card-title">${title}</div></div>
                ${actions ? `<div class="card-actions">${actions}</div>` : ''}
            </div>
            <div class="card-body">${body}</div>
        </section>
    `;
}

function metricCard(label, value, sublabel) {
    return `
        <article class="metric-card">
            <div class="metric-label">${label}</div>
            <div class="metric-value">${value}</div>
            <div class="metric-sub">${sublabel}</div>
        </article>
    `;
}

function stateBlock(kind, title, copy) {
    return `
        <div class="state-block ${kind}">
            <div class="state-kicker">${kind}</div>
            <div class="state-title">${title}</div>
            <div class="state-copy">${copy}</div>
        </div>
    `;
}

function renderNav(active) {
    document.getElementById('nav').innerHTML = sections.map(section => `
        <button class="nav-item ${section.id === active ? 'active' : ''}" data-section="${section.id}">
            <span>${section.label}</span>
        </button>
    `).join('');

    document.querySelectorAll('.nav-item').forEach(button => {
        button.addEventListener('click', () => renderPage(button.dataset.section));
    });
}

function deriveOwner(providers) {
    const builder = providers.find(item => item.role === 'builder') || providers[0];
    if (!builder) return 'Unassigned';
    return `${titleCase(builder.role)} · ${titleCase(builder.provider)}`;
}

function deriveWorkflowSummary(item, proposals) {
    if (item.context && typeof item.context === 'object') {
        const title = item.context.title || item.context.goal || item.context.scope;
        if (title) return String(title);
    }
    if (proposals.length > 0 && proposals[proposals.length - 1].prompt) {
        return proposals[proposals.length - 1].prompt;
    }
    return item.summary || 'Workflow loaded from backend with read-only orchestration summary.';
}

function hydrateWorkflows(workflows) {
    const sorted = [...workflows].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
    return sorted.map((item, index) => {
        const roleAssignments = Array.isArray(item.role_assignments) && item.role_assignments.length
            ? item.role_assignments.map(entry => ({
                role: entry.role,
                provider: entry.configured_provider,
                model: entry.configured_model,
                resolved_provider: entry.resolved_provider,
                is_live: entry.is_live,
                is_available: entry.is_available,
            }))
            : [];
        const providers = roleAssignments.length
            ? roleAssignments
            : Array.isArray(item.providers) && item.providers.length
                ? item.providers
                : [
                    { role: 'reviewer', provider: 'openai', model: 'gpt-4o' },
                    { role: 'planner', provider: 'openai', model: 'gpt-4o' },
                    { role: 'builder', provider: 'anthropic', model: 'claude-sonnet-4-20250514' },
                ];
        const proposals = Array.isArray(item.proposals) ? item.proposals : [];
        return {
            id: item.id,
            status: item.status,
            approval_mode: item.approval_mode || 'auto_with_limits',
            workflow_mode: item.workflow_mode || 'normal',
            mode_label: item.resolved_policy?.label || titleCase(item.workflow_mode || 'normal'),
            resolved_policy: item.resolved_policy || null,
            priority: index === 0 ? 'High' : index === 1 ? 'Medium' : 'Low',
            updated_at: formatRelative(item.updated_at),
            owner: deriveOwner(providers),
            summary: deriveWorkflowSummary(item, proposals),
            providers,
            proposals,
            summary_obj: item.summary || null,
            demo_events: Array.isArray(item.demo_events) ? item.demo_events : [],
            current_stage: item.current_stage || item.status,
            is_demo: Boolean(item.is_demo),
        };
    });
}

function selectedWorkflow(dataset) {
    if (!dataset.workflows.length) return null;
    return dataset.workflows.find(item => item.id === state.selectedWorkflowId) || dataset.workflows[0];
}

function providerFocus(role) {
    if (role === 'reviewer') return 'Scoping, critique, proposal shaping';
    if (role === 'planner') return 'Planning, sequencing, workflow framing';
    return 'Execution, implementation, structured output';
}

function buildSummary(workflows, usingBackend) {
    if (!usingBackend) return mock.summary;
    const proposals = workflows.flatMap(item => item.proposals || []);
    const pendingApprovals = proposals.filter(item => item.approval === 'pending').length;
    const totalTokens = proposals.reduce((sum, item) => sum + (item.token_count || 0), 0);
    const approved = proposals.filter(item => ['approved', 'auto_approved'].includes(item.approval)).length;
    const approvalRate = proposals.length ? `${Math.round((approved / proposals.length) * 100)}%` : '—';
    return {
        liveWorkflows: workflows.length,
        openProposals: proposals.length,
        pendingApprovals,
        experimentWinRate: '—',
        tokensToday: totalTokens ? `${Math.round(totalTokens / 1000)}k` : '0',
        approvalRate,
    };
}

function buildProposals(workflows, usingBackend) {
    const items = workflows.flatMap(workflow =>
        (workflow.proposals || []).map(item => ({
            id: item.id,
            workflowId: workflow.id,
            proposalId: item.id,
            batch: `Batch ${item.batch_index ?? 0}`,
            title: workflow.id,
            prompt: item.prompt || 'No proposal prompt available.',
            files: item.files_affected || [],
            tokens: item.token_count ? `${(item.token_count / 1000).toFixed(1)}k` : '—',
            approval: item.approval || 'pending',
        }))
    );
    return items.length ? items : (usingBackend ? [] : mock.workflows.flatMap(item => item.proposals.map(prop => ({
        id: prop.id,
        batch: `Batch ${prop.batch_index}`,
        title: item.id,
        prompt: prop.prompt,
        files: prop.files_affected,
        tokens: `${(prop.token_count / 1000).toFixed(1)}k`,
        approval: prop.approval,
    }))));
}

function buildApprovalTitle(workflow, proposal) {
    return proposal.prompt ? proposal.prompt.slice(0, 72) : `Proposal ${proposal.id}`;
}

function buildApprovalSummary(workflow, proposal) {
    if (proposal.response) return proposal.response;
    return workflow.summary || 'Awaiting approval review.';
}

function buildApprovalRationale(workflow, proposal) {
    if (proposal.reviewer_notes) return proposal.reviewer_notes;
    return `Proposal from ${workflow.id} remains in a safe approval-only state.`;
}

function approvalItemsFromWorkflows(workflows) {
    return workflows.flatMap(workflow =>
        (workflow.proposals || []).map(proposal => ({
            workflowId: workflow.id,
            proposalId: proposal.id,
            workflow: workflow.id,
            title: buildApprovalTitle(workflow, proposal),
            summary: buildApprovalSummary(workflow, proposal),
            note: buildApprovalRationale(workflow, proposal),
            scope: `${(proposal.files_affected || []).length} file${(proposal.files_affected || []).length === 1 ? '' : 's'} · approval only`,
            age: formatRelative(proposal.resolved_at || proposal.created_at || workflow.updated_at),
            risk: proposal.approval === 'pending' ? 'Pending' : proposal.approval,
            approval: proposal.approval || 'pending',
        }))
    );
}

function buildApprovals(workflows, usingBackend) {
    const items = approvalItemsFromWorkflows(workflows);
    if (items.length) return usingBackend ? items : items.filter(item => item.approval === 'pending');
    return usingBackend ? [] : [
        {
            workflowId: 'wf-exp-087',
            proposalId: 'prop-201',
            title: 'Approve workflow cutoff rules',
            workflow: 'wf-exp-087',
            risk: 'Low',
            scope: '2 files · approval only',
            age: '9m',
            note: 'Read-only preview retained. No runtime actions exposed.',
            summary: 'Proposal batch paused for operator sign-off on experiment cutoff parameters.',
            approval: 'pending',
        },
        {
            workflowId: 'wf-control-142',
            proposalId: 'prop-202',
            title: 'Approve provider label refresh',
            workflow: 'wf-control-142',
            risk: 'Low',
            scope: '1 file · approval only',
            age: '3m',
            note: 'Presentation-only change for role naming consistency.',
            summary: 'Compact approval framing for mobile review and queue scan speed.',
            approval: 'pending',
        },
    ];
}

function buildExperiments(workflows, usingBackend) {
    const items = workflows
        .filter(item => item.summary_obj)
        .map(item => ({
            title: item.summary_obj.title || item.id,
            outcome: item.summary_obj.outcome || item.status,
            progress: `${item.summary_obj.completed_batches || 0} / ${item.summary_obj.total_batches || 0} batches`,
            tokens: item.summary_obj.total_tokens ? `${Math.round(item.summary_obj.total_tokens / 1000)}k` : '0',
            files: `${(item.summary_obj.files_changed || []).length} files`,
            note: item.summary || 'Experiment summary placeholder.',
        }));
    return items.length ? items : (usingBackend ? workflows.map(item => ({
        title: item.id,
        outcome: item.status,
        progress: `${(item.proposals || []).length} proposals`,
        tokens: '—',
        files: '—',
        note: item.summary,
    })) : []);
}

function buildProviders(workflows, usingBackend) {
    const map = new Map();
    workflows.forEach(workflow => {
        (workflow.providers || []).forEach(item => {
            if (!map.has(item.role)) {
                map.set(item.role, {
                    role: titleCase(item.role),
                    provider: titleCase(item.provider),
                    model: item.model,
                    status: usingBackend ? (item.is_live === false ? 'fallback' : 'online') : 'ready',
                    focus: providerFocus(item.role),
                });
            }
        });
    });
    if (map.size) return Array.from(map.values());
    return [
        { role: 'Reviewer', provider: 'OpenAI', model: 'gpt-4o', status: usingBackend ? 'degraded' : 'ready', focus: providerFocus('reviewer') },
        { role: 'Planner', provider: 'OpenAI', model: 'gpt-4o', status: usingBackend ? 'degraded' : 'ready', focus: providerFocus('planner') },
        { role: 'Builder', provider: 'Anthropic', model: 'claude-sonnet-4-20250514', status: usingBackend ? 'degraded' : 'ready', focus: providerFocus('builder') },
    ];
}

function buildOperators(usingBackend) {
    if (!usingBackend) return mock.operators;
    const queueCount = state.queue?.total || 0;
    const sessionCount = state.sessions?.total || 0;
    const ideaCount = state.ideas?.count || 0;
    return [
        { name: 'Control tower', state: 'online', detail: 'Backend-connected read-only dashboard is active.' },
        { name: 'Dispatch queue', state: queueCount ? 'active' : 'ready', detail: queueCount ? `${queueCount} builder job${queueCount === 1 ? '' : 's'} visible across pending, running, and terminal states.` : 'No builder jobs are currently queued.' },
        { name: 'Session manager', state: sessionCount ? 'active' : 'ready', detail: sessionCount ? `${sessionCount} work session${sessionCount === 1 ? '' : 's'} tracked for local Claude / Antigravity coordination.` : 'No work sessions are currently registered.' },
        { name: 'Idea intake', state: ideaCount ? 'active' : 'ready', detail: ideaCount ? `${ideaCount} structured idea thread${ideaCount === 1 ? '' : 's'} available for discussion and refinement.` : 'No idea threads have been created yet.' },
    ];
}

function getDataset() {
    const usingBackend = state.apiMode === 'online';
    const workflows = usingBackend ? hydrateWorkflows(state.workflows) : mock.workflows;
    return {
        usingBackend,
        workflows,
        queue: usingBackend ? state.queue : null,
        sessions: usingBackend ? state.sessions : null,
        ideas: usingBackend ? state.ideas : null,
        summary: buildSummary(workflows, usingBackend),
        proposals: buildProposals(workflows, usingBackend),
        approvals: buildApprovals(workflows, usingBackend),
        experiments: buildExperiments(workflows, usingBackend),
        providers: buildProviders(workflows, usingBackend),
        operators: buildOperators(usingBackend),
    };
}

function renderOperators() {
    const dataset = getDataset();
    document.getElementById('operator-stack').innerHTML = dataset.operators.map(item => `
        <div class="operator-card">
            <div class="operator-row">
                <div class="operator-name">${escapeHtml(item.name)}</div>
                <span class="${badgeClass(item.state)}">${escapeHtml(item.state)}</span>
            </div>
            <div class="operator-detail">${escapeHtml(item.detail)}</div>
        </div>
    `).join('');
}

function renderOverview(dataset) {
    const newestId = dataset.workflows.length ? dataset.workflows[0].id : null;
    const systemRows = dataset.workflows.length
        ? dataset.workflows.map(item => {
            const proposalCount = (item.proposals || []).length;
            const isNewest = item.id === newestId;
            return `
            <div class="list-row soft overview-row ${isNewest ? 'newest-row' : ''}" data-nav-workflow="${escapeHtml(item.id)}">
                <div>
                    <div class="list-title">${escapeHtml(item.id)}${isNewest ? ' <span class="newest-badge">latest</span>' : ''}</div>
                    <div class="list-copy">${escapeHtml(item.summary)}</div>
                    <div class="overview-meta-chips">
                        ${proposalCount ? `<span class="meta-chip">${proposalCount} proposal${proposalCount === 1 ? '' : 's'}</span>` : ''}
                        <span class="meta-chip">${escapeHtml(item.updated_at)}</span>
                    </div>
                </div>
                <div class="row-meta">
                    ${item.is_demo ? '<span class="demo-badge">demo</span>' : ''}
                    <span class="${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
                </div>
            </div>
        `}).join('')
        : `<div class="list-row soft"><div><div class="list-title">No workflows loaded</div><div class="list-copy">Backend returned no workflows. The shell remains stable and read only.</div></div></div>`;

    const stateContent = state.apiMode === 'loading'
        ? stateBlock('loading', 'Loading shell', 'Hydrating premium dashboard cards from backend read-only endpoints.')
        : state.apiMode === 'online'
            ? stateBlock('loading', 'Backend connected', 'Live read-only data is active for workflow and overview panels.')
            : stateBlock('error', 'Backend unavailable', 'Falling back to mock-safe data while preserving premium layout and visibility.');

    return `
        <div class="metrics-grid">
            ${metricCard('Live workflows', dataset.summary.liveWorkflows, 'Read-only orchestration visibility across active workflow state')}
            ${metricCard('Open proposals', dataset.summary.openProposals, 'Proposal summaries only — no mutation controls exposed')}
            ${metricCard('Tokens today', dataset.summary.tokensToday, 'Derived from visible workflow/proposal metadata when available')}
            ${metricCard('Approval rate', dataset.summary.approvalRate, 'Safe summary placeholder for operator scan speed')}
        </div>
        <div class="content-grid two-up">
            ${card('System overview', `<div class="list-stack">${systemRows}</div>`)}
            ${card('Polished states', `<div class="state-grid">${stateContent}${stateBlock('empty', 'No live mutations', 'Approve/reject controls update proposal state only. No execution controls are mounted.')}${stateBlock('loading', 'Mock-safe continuity', 'If the backend disappears, the shell continues rendering with graceful placeholder data.')}</div>`)}
        </div>
    `;
}

function renderTimeline(workflow) {
    if (!workflow) return '<div class="empty-panel">No workflow selected for end-to-end demo inspection.</div>';
    if (!workflow.demo_events.length) return '<div class="empty-panel">No timeline events recorded yet. Create and run a demo workflow to populate the orchestrator loop.</div>';
    return `
        <div class="list-stack">
            ${workflow.demo_events.map(item => `
                <div class="list-row bordered">
                    <div>
                        <div class="list-title">${escapeHtml(titleCase(item.stage))} · ${escapeHtml(titleCase(item.role))}</div>
                        <div class="list-copy">${escapeHtml(item.summary)}</div>
                    </div>
                    <div class="approval-meta">
                        <span class="${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
                        <span class="meta-text">${escapeHtml(item.provider || 'system')}</span>
                        <span class="meta-text">${escapeHtml(item.model || 'local')}</span>
                        <span class="${badgeClass(item.is_mock ? 'fallback' : 'online')}">${item.is_mock ? 'mock' : 'live'}</span>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function renderRoleAssignments(workflow) {
    if (!workflow) return '<div class="empty-panel">No provider mapping available.</div>';
    return `
        <div class="provider-grid">
            ${(workflow.providers || []).map(item => `
                <article class="provider-card">
                    <div class="operator-row">
                        <div>
                            <div class="provider-role">${escapeHtml(titleCase(item.role))}</div>
                            <div class="provider-name">${escapeHtml(titleCase(item.provider || item.resolved_provider || 'unknown'))}</div>
                        </div>
                        <span class="${badgeClass(item.is_live === false ? 'fallback' : 'online')}">${item.is_live === false ? 'mock' : 'live'}</span>
                    </div>
                    <div class="provider-model">${escapeHtml(item.model || 'default')}</div>
                    <div class="list-copy">${escapeHtml(item.resolved_provider ? `Resolved as ${item.resolved_provider}` : providerFocus(item.role))}</div>
                </article>
            `).join('')}
        </div>
    `;
}

function renderModePolicy(workflow) {
    if (!workflow || !workflow.resolved_policy) return '<div class="empty-panel">No resolved workflow mode policy available.</div>';
    const policy = workflow.resolved_policy;
    const wfId = workflow.id || '';
    const currentMode = workflow.workflow_mode || 'normal';
    const modeOptions = ['compact', 'normal', 'rich', 'go_wild'];
    const modeSelect = wfId ? `
        <select class="mode-select workflow-mode-change" data-workflow-id="${escapeHtml(wfId)}" style="margin-left:8px;font-size:0.76rem">
            ${modeOptions.map(m => `<option value="${m}" ${m === currentMode ? 'selected' : ''}>${titleCase(m.replace('_', ' '))}</option>`).join('')}
        </select>` : '';
    return `
        <div class="session-detail-grid">
            <div><span class="meta-label">Mode</span>${modeBadgeHtml(currentMode, workflow.mode_label)}${modeSelect}</div>
            <div><span class="meta-label">Cost</span><span>${escapeHtml(policy.budgets?.cost_intensity || 'balanced')}</span></div>
            <div><span class="meta-label">Context</span><span>${escapeHtml(policy.context?.context_detail || 'standard')}</span></div>
            <div><span class="meta-label">Compression</span><span>${escapeHtml(policy.compression?.summarization || 'balanced')}</span></div>
            <div><span class="meta-label">Fan-out</span><span>${escapeHtml(String(policy.parallelism?.session_fan_out ?? 1))}</span></div>
            <div><span class="meta-label">Review depth</span><span>${escapeHtml(policy.review?.review_depth || 'standard')}</span></div>
            <div><span class="meta-label">Planner tokens</span><span>${escapeHtml(String(policy.budgets?.planner_max_tokens ?? '—'))}</span></div>
            <div><span class="meta-label">Builder tokens</span><span>${escapeHtml(String(policy.budgets?.builder_max_tokens ?? '—'))}</span></div>
        </div>
    `;
}

function renderDemoWorkflowPanel(dataset) {
    const workflow = selectedWorkflow(dataset);
    return `
        <div class="content-grid two-up">
            ${card('Demo workflow loop', renderTimeline(workflow), '<span class="card-hint">pending → planning → building → reviewing → approved / blocked → completed</span>')}
            ${card('Role assignments', renderRoleAssignments(workflow))}
            ${card('Workflow mode policy', renderModePolicy(workflow), '<span class="card-hint">Resolved policy currently active for the selected workflow</span>')}
        </div>
    `;
}

function renderWorkflowState(dataset) {
    const body = dataset.workflows.length
        ? dataset.workflows.map(item => `
            <div class="workflow-shell-card ${item.id === state.selectedWorkflowId ? 'selected-workflow' : ''}" data-workflow-id="${escapeHtml(item.id)}">
                <div class="workflow-shell-top">
                    <div>
                        <div class="list-title">${escapeHtml(item.id)}</div>
                        <div class="list-copy">${escapeHtml(item.summary)}</div>
                    </div>
                    <div class="row-meta">
                        ${item.is_demo ? '<span class="demo-badge">demo</span>' : ''}
                        <span class="${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
                    </div>
                </div>
                <div class="workflow-meta-grid">
                    <div><span class="meta-label">Owner</span><span>${escapeHtml(item.owner)}</span></div>
                    <div><span class="meta-label">Approval</span><span>${escapeHtml(item.approval_mode)}</span></div>
                    <div><span class="meta-label">Workflow mode</span>${modeBadgeHtml(item.workflow_mode || 'normal', item.mode_label)}</div>
                    <div><span class="meta-label">Stage</span><span>${escapeHtml(item.current_stage || item.status)}</span></div>
                    <div><span class="meta-label">Updated</span><span>${escapeHtml(item.updated_at)}</span></div>
                </div>
            </div>
        `).join('')
        : `<div class="empty-panel">No workflow state available from the backend yet.</div>`;
    return `
        ${card('Workflow state', `<div class="list-stack">${body}</div>`, '<span class="card-hint">Select a workflow to inspect the end-to-end demo loop</span>')}
        ${renderDemoWorkflowPanel(dataset)}
    `;
}

function queueStatusCards(queue) {
    return `
        <div class="queue-state-grid">
            ${stateBlock('loading', 'Pending', `${queue?.pending || 0} job${(queue?.pending || 0) === 1 ? '' : 's'} awaiting worker claim.`)}
            ${stateBlock('loading', 'Running', `${queue?.running || 0} job${(queue?.running || 0) === 1 ? '' : 's'} currently claimed by a local worker.`)}
            ${stateBlock('empty', 'Completed', `${queue?.completed || 0} job${(queue?.completed || 0) === 1 ? '' : 's'} finished with structured results.`)}
            ${stateBlock('error', 'Failed', `${queue?.failed || 0} job${(queue?.failed || 0) === 1 ? '' : 's'} ended with failure metadata available for inspection.`)}
        </div>
    `;
}

function idleSessions() {
    if (!state.sessions || !Array.isArray(state.sessions.sessions)) return [];
    return state.sessions.sessions.filter(s => s.status === 'idle');
}

function pendingJobs() {
    if (!state.queue || !Array.isArray(state.queue.items)) return [];
    return state.queue.items.filter(j => j.status === 'pending');
}

function assignSessionSelect(jobId) {
    const idle = idleSessions();
    if (!idle.length) return '<span class="meta-text">No idle sessions</span>';
    const assigning = state.assignActionKey === jobId;
    return `<select class="assign-session-select" data-assign-job-id="${escapeHtml(jobId)}" ${assigning || state.apiMode !== 'online' ? 'disabled' : ''}>
        <option value="">Assign to\u2026</option>
        ${idle.map(s => `<option value="${escapeHtml(s.session_id)}">${escapeHtml(s.session_id)}</option>`).join('')}
    </select>`;
}

function assignedSessionForJob(jobId) {
    if (!state.sessions || !Array.isArray(state.sessions.sessions)) return null;
    return state.sessions.sessions.find(session => session.assigned_job_id === jobId) || null;
}

function renderPromptPreviewCard() {
    const preview = state.promptPreview;
    if (!preview) return '';
    const sessionId = preview.session?.session_id || '';
    const canDeliver = preview.session?.status === 'assigned' || preview.session?.status === 'idle';
    const delivering = state.deliveryActionKey === sessionId;
    return card('Prompt preview', `
        <div class="prompt-preview-callout">Preview only — this shows the exact prompt payload and intended session target. No delivery or execution occurs.</div>
        <div class="prompt-preview-grid">
            <div><span class="meta-label">Session</span><span>${escapeHtml(sessionId)}</span></div>
            <div><span class="meta-label">Role</span><span>${escapeHtml(titleCase(preview.session?.role || ''))}</span></div>
            <div><span class="meta-label">Workflow</span><span>${escapeHtml(preview.workflow?.workflow_id || preview.job?.workflow_id || '')}</span></div>
            <div><span class="meta-label">Mode</span><span>${escapeHtml(preview.workflow?.workflow_mode || '')}</span></div>
            <div><span class="meta-label">Proposal</span><span>${escapeHtml(preview.job?.proposal_id || '')}</span></div>
            <div><span class="meta-label">Job</span><span>${escapeHtml(preview.job?.job_id || '')}</span></div>
            <div><span class="meta-label">Token estimate</span><span>${escapeHtml(String(preview.prompt?.token_estimate || '0'))}</span></div>
            <div><span class="meta-label">Status</span><span>${escapeHtml(preview.session?.status || '')}</span></div>
            <div class="session-detail-wide"><span class="meta-label">Expected next action</span><span>${escapeHtml(preview.session?.next_expected_action || '')}</span></div>
            <div class="session-detail-wide"><span class="meta-label">Return format</span><span>${escapeHtml(Array.isArray(preview.prompt?.expected_return_format) ? preview.prompt.expected_return_format.join(' \u00b7 ') : '')}</span></div>
        </div>
        <pre class="prompt-preview-text">${escapeHtml(preview.prompt?.prompt_text || '')}</pre>
        ${canDeliver ? `<div class="prompt-delivery-action"><button class="btn btn-deliver" type="button" data-deliver-session-id="${escapeHtml(sessionId)}" ${delivering || state.apiMode !== 'online' ? 'disabled' : ''}>${delivering ? 'Marking\u2026' : 'Mark as delivered'}</button><span class="meta-text delivery-hint">Records that you manually delivered this prompt to the session. No automated sending.</span></div>` : ''}
    `, '<span class="card-hint">Exact final prompt destined for the assigned session</span>');
}

function queueRows(dataset) {
    if (state.apiMode === 'loading') {
        return '<div class="empty-panel">Loading dispatch queue state from builder job endpoints…</div>';
    }
    if (state.apiMode !== 'online') {
        return '<div class="empty-panel">Backend unavailable. Queue view is showing no live builder job data.</div>';
    }
    if (!dataset.queue || !Array.isArray(dataset.queue.items) || !dataset.queue.items.length) {
        return '<div class="empty-panel">No builder jobs have been dispatched yet.</div>';
    }
    return `
        <div class="table-shell">
            <div class="queue-table-row table-head">
                <span>Job</span>
                <span>Workflow / proposal</span>
                <span>Updated</span>
                <span>Status</span>
            </div>
            ${dataset.queue.items.map(item => {
                const assignedSession = assignedSessionForJob(item.job_id);
                const previewLoading = state.promptPreviewLoading === assignedSession?.session_id;
                return `
                <div class="queue-table-row${item.job_id === state.lastDispatchedJobId ? ' dispatched-highlight' : ''}">
                    <div>
                        <div class="list-title">${escapeHtml(item.job_id)} · ${escapeHtml(titleCase(item.category))}</div>
                        <div class="list-copy">${escapeHtml(item.summary || 'No builder summary yet.')}</div>
                        <div class="file-pill-wrap queue-meta-wrap">
                            <span class="meta-chip">Created ${escapeHtml(formatRelative(item.created_at))}</span>
                            ${item.started_at ? `<span class="meta-chip">Started ${escapeHtml(formatRelative(item.started_at))}</span>` : ''}
                            ${item.completed_at ? `<span class="meta-chip">Completed ${escapeHtml(formatRelative(item.completed_at))}</span>` : ''}
                            ${item.worker_id ? `<span class="meta-chip">Worker ${escapeHtml(item.worker_id)}</span>` : ''}
                            ${assignedSession ? `<span class="meta-chip">Session ${escapeHtml(assignedSession.session_id)}</span>` : ''}
                        </div>
                    </div>
                    <div class="queue-link-stack">
                        <a class="nav-link" data-nav-workflow="${escapeHtml(item.workflow.workflow_id)}" href="#">${escapeHtml(item.workflow.workflow_id)}</a>
                        <span class="meta-text">${escapeHtml(item.workflow.proposal_id)}</span>
                    </div>
                    <div class="queue-link-stack">
                        <span class="meta-text">${escapeHtml(formatRelative(item.updated_at))}</span>
                        ${item.output_ref ? `<span class="meta-text">${escapeHtml(item.output_ref)}</span>` : ''}
                        ${item.error ? `<span class="meta-text">${escapeHtml(item.error)}</span>` : ''}
                    </div>
                    <div class="queue-status-stack">
                        <span class="${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
                        <span class="${badgeClass(item.approval_status === 'auto_approved' ? 'approved' : item.approval_status)}">${escapeHtml(item.approval_status)}</span>
                        <span class="meta-text">${escapeHtml(`${(item.artifacts || []).length} artifact${(item.artifacts || []).length === 1 ? '' : 's'}`)}</span>
                        ${item.status === 'pending' ? assignSessionSelect(item.job_id) : ''}
                        ${assignedSession ? `<button class="btn btn-preview" type="button" data-preview-session-id="${escapeHtml(assignedSession.session_id)}" ${previewLoading || state.apiMode !== 'online' ? 'disabled' : ''}>${previewLoading ? 'Loading…' : 'Preview prompt'}</button>` : ''}
                    </div>
                </div>`;
            }).join('')}
        </div>
    `;
}

function renderQueue(dataset) {
    const queue = dataset.queue;
    return `
        <div class="content-grid two-up">
            ${card('Dispatch queue', queueRows(dataset), '<span class="card-hint">Read-only visibility into builder job dispatch, claim, and terminal state</span>')}
            ${card('Queue state', queueStatusCards(queue || { pending: 0, running: 0, completed: 0, failed: 0 }), '<span class="card-hint">Pending → running → completed / failed</span>')}
        </div>
    `;
}

function selectedSession(dataset) {
    if (!dataset.sessions || !Array.isArray(dataset.sessions.sessions) || !dataset.sessions.sessions.length) return null;
    const summary = dataset.sessions.sessions.find(item => item.session_id === state.selectedSessionId) || dataset.sessions.sessions[0];
    return state.sessionDetails[summary.session_id] || summary;
}

function sessionStatusCards(registry) {
    const waitingPrompt = registry?.waiting_for_prompt_delivery || 0;
    const waitingResult = registry?.waiting_for_result || 0;
    return `
        <div class="session-state-grid">
            ${stateBlock('empty', 'Idle', `${registry?.idle || 0} session${(registry?.idle || 0) === 1 ? '' : 's'} waiting without an assigned job.`)}
            ${stateBlock('loading', 'Assigned / prompt', `${(registry?.assigned || 0) + waitingPrompt} session${(((registry?.assigned || 0) + waitingPrompt) === 1) ? '' : 's'} assigned or waiting for prompt delivery.`)}
            ${stateBlock('loading', 'Running / result', `${(registry?.running || 0) + waitingResult} session${(((registry?.running || 0) + waitingResult) === 1) ? '' : 's'} running or waiting for result capture.`)}
            ${stateBlock('error', 'Blocked / failed', `${(registry?.blocked || 0) + (registry?.failed || 0)} session${(((registry?.blocked || 0) + (registry?.failed || 0)) === 1) ? '' : 's'} need operator attention.`)}
        </div>
    `;
}

function sessionRows(dataset) {
    if (state.apiMode === 'loading') {
        return '<div class="empty-panel">Loading local session registry…</div>';
    }
    if (state.apiMode !== 'online') {
        return '<div class="empty-panel">Backend unavailable. Session registry data is not currently loaded.</div>';
    }
    if (!dataset.sessions || !Array.isArray(dataset.sessions.sessions) || !dataset.sessions.sessions.length) {
        return '<div class="empty-panel">No Claude / Antigravity sessions are registered yet.</div>';
    }
    return `
        <div class="list-stack">
            ${dataset.sessions.sessions.map(item => `
                <div class="workflow-shell-card ${item.session_id === state.selectedSessionId ? 'selected-workflow' : ''}" data-session-id="${escapeHtml(item.session_id)}">
                    <div class="workflow-shell-top">
                        <div>
                            <div class="list-title">${escapeHtml(item.session_id)}</div>
                            <div class="list-copy">${escapeHtml(item.next_expected_action || 'Await operator action.')}</div>
                        </div>
                        <span class="${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
                    </div>
                    <div class="workflow-meta-grid">
                        <div><span class="meta-label">Role</span><span>${escapeHtml(titleCase(item.role))}</span></div>
                        <div><span class="meta-label">Job</span><span>${escapeHtml(item.assigned_job_id || 'Unassigned')}</span></div>
                        <div><span class="meta-label">Last activity</span><span>${escapeHtml(formatRelative(item.last_activity_at))}</span></div>
                        <div><span class="meta-label">Lifecycle</span><span>${escapeHtml(String(item.lifecycle_count || 0))} events</span></div>
                    </div>
                    <div class="list-copy session-row-summary">${escapeHtml(item.last_result_summary || 'No result recorded')}</div>
                </div>
            `).join('')}
        </div>
    `;
}

function renderSessionResultForm(session) {
    if (!session || state.resultFormSessionId !== session.session_id) return '';
    const saving = state.resultActionKey === session.session_id;
    return `
        <div class="input-panel result-entry-panel">
            <div class="result-form-grid">
                <div>
                    <label class="meta-label" for="result-outcome">Outcome</label>
                    <select id="result-outcome" class="assign-session-select">
                        <option value="success" ${state.resultDraft.outcome === 'success' ? 'selected' : ''}>success</option>
                        <option value="partial_success" ${state.resultDraft.outcome === 'partial_success' ? 'selected' : ''}>partial_success</option>
                        <option value="needs_followup" ${state.resultDraft.outcome === 'needs_followup' ? 'selected' : ''}>needs_followup</option>
                        <option value="blocked" ${state.resultDraft.outcome === 'blocked' ? 'selected' : ''}>blocked</option>
                        <option value="failed" ${state.resultDraft.outcome === 'failed' ? 'selected' : ''}>failed</option>
                    </select>
                </div>
                <div>
                    <label class="meta-label" for="result-next-action">Next suggested action</label>
                    <input id="result-next-action" class="input-field" type="text" value="${escapeHtml(state.resultDraft.nextAction)}">
                </div>
            </div>
            <div>
                <label class="meta-label" for="result-summary">Summary</label>
                <textarea id="result-summary" class="input-textarea" placeholder="What happened?">${escapeHtml(state.resultDraft.summary)}</textarea>
            </div>
            <div>
                <label class="meta-label" for="result-notes">Notes</label>
                <textarea id="result-notes" class="input-textarea" placeholder="Structured details, blockers, follow-up notes...">${escapeHtml(state.resultDraft.notes)}</textarea>
            </div>
            <div>
                <label class="meta-label" for="result-artifact">Artifact / reference (optional)</label>
                <input id="result-artifact" class="input-field" type="text" value="${escapeHtml(state.resultDraft.artifactRef)}" placeholder="log path, note, output ref...">
            </div>
            <div class="input-actions">
                <button class="btn" type="button" data-result-cancel-session-id="${escapeHtml(session.session_id)}" ${saving ? 'disabled' : ''}>Cancel</button>
                <button class="btn btn-deliver" type="button" data-result-save-session-id="${escapeHtml(session.session_id)}" ${saving || state.apiMode !== 'online' ? 'disabled' : ''}>${saving ? 'Saving…' : 'Save result'}</button>
            </div>
        </div>
    `;
}

function renderSessionDetail(dataset) {
    const session = selectedSession(dataset);
    if (!session) {
        return '<div class="empty-panel">Select or register a session to inspect assigned job, last result, and next expected action.</div>';
    }
    const lifecycle = Array.isArray(session.lifecycle) && session.lifecycle.length
        ? `<div class="list-stack compact">${session.lifecycle.slice().reverse().slice(0, 6).map(item => `
            <div class="list-row soft">
                <div>
                    <div class="list-title">${escapeHtml(titleCase(item.event_type || 'event'))}</div>
                    <div class="list-copy">${escapeHtml(item.note || 'Lifecycle event recorded.')}</div>
                </div>
                <div class="row-meta stacked">
                    <span class="${badgeClass(item.status || 'unknown')}">${escapeHtml(item.status || 'unknown')}</span>
                    <span class="meta-text">${escapeHtml(formatRelative(item.recorded_at))}</span>
                </div>
            </div>
        `).join('')}</div>`
        : '<div class="empty-panel">No lifecycle events recorded yet.</div>';
    const canRecordResult = ['running', 'waiting_for_result'].includes(session.status);
    const resultButtonOpen = state.resultFormSessionId === session.session_id;
    return `
        <div class="content-grid">
            <div class="session-detail-grid">
                <div><span class="meta-label">Session ID</span><span>${escapeHtml(session.session_id)}</span></div>
                <div><span class="meta-label">Role</span><span>${escapeHtml(titleCase(session.role))}</span></div>
                <div><span class="meta-label">Assigned job</span><span>${escapeHtml(session.assigned_job_id || 'None')}</span></div>
                <div><span class="meta-label">Status</span><span>${escapeHtml(session.status)}</span></div>
                <div><span class="meta-label">Assigned at</span><span>${escapeHtml(session.assigned_at ? formatRelative(session.assigned_at) : 'Not assigned')}</span></div>
                <div><span class="meta-label">Last activity</span><span>${escapeHtml(formatRelative(session.last_activity_at))}</span></div>
                <div class="session-detail-wide"><span class="meta-label">Next expected action</span><span>${escapeHtml(session.next_expected_action || 'Await operator update')}</span></div>
                <div class="session-detail-wide"><span class="meta-label">Last result summary</span><span>${escapeHtml(session.last_result_summary || 'No result summary recorded yet.')}</span></div>
            </div>
            ${session.status === 'idle' ? (() => {
                const jobs = pendingJobs();
                const assigning = state.assignActionKey === session.session_id;
                return jobs.length ? `<div class="session-assign-action"><select class="assign-job-select" data-assign-session-id="${escapeHtml(session.session_id)}" ${assigning || state.apiMode !== 'online' ? 'disabled' : ''}><option value="">Assign a pending job\u2026</option>${jobs.map(j => `<option value="${escapeHtml(j.job_id)}">${escapeHtml(j.job_id)} \u00b7 ${escapeHtml(titleCase(j.category))}</option>`).join('')}</select></div>` : '<div class="session-assign-action"><span class="meta-text">No pending jobs to assign.</span></div>';
            })() : ''}
            ${session.assigned_job_id ? `<div class="session-assign-action session-action-row"><button class="btn btn-preview" type="button" data-preview-session-id="${escapeHtml(session.session_id)}" ${state.promptPreviewLoading === session.session_id || state.apiMode !== 'online' ? 'disabled' : ''}>${state.promptPreviewLoading === session.session_id ? 'Loading…' : 'Preview prompt'}</button>${canRecordResult ? `<button class="btn btn-record-result" type="button" data-open-result-session-id="${escapeHtml(session.session_id)}">${resultButtonOpen ? 'Hide result form' : 'Record result'}</button>` : ''}</div>` : ''}
            ${state.promptPreviewSessionId === session.session_id ? renderPromptPreviewCard() : ''}
            ${renderSessionResultForm(session)}
            ${card('Recent lifecycle', lifecycle, '<span class="card-hint">Typed local session history</span>')}
        </div>
    `;
}

function renderSessions(dataset) {
    const registry = dataset.sessions;
    return `
        <div class="content-grid two-up">
            ${card('Session list', sessionRows(dataset), '<span class="card-hint">Local Claude / Antigravity session registry</span>')}
            ${card('Session detail', `${renderSessionDetail(dataset)}${sessionStatusCards(registry || { idle: 0, assigned: 0, running: 0, blocked: 0, failed: 0 })}`, '<span class="card-hint">Assigned job, last result, and next expected action</span>')}
        </div>
    `;
}

function selectedIdea(dataset) {
    if (!dataset.ideas || !Array.isArray(dataset.ideas.ideas) || !dataset.ideas.ideas.length) return null;
    return dataset.ideas.ideas.find(item => item.id === state.selectedIdeaId) || dataset.ideas.ideas[0];
}

function ideaRows(dataset) {
    if (state.apiMode === 'loading') {
        return '<div class="empty-panel">Loading idea threads and discussion history…</div>';
    }
    if (state.apiMode !== 'online') {
        return '<div class="empty-panel">Backend unavailable. Idea threads are not currently loaded.</div>';
    }
    if (!dataset.ideas || !Array.isArray(dataset.ideas.ideas) || !dataset.ideas.ideas.length) {
        return '<div class="empty-panel">No idea threads exist yet. Create one through the backend API to start structured discussion.</div>';
    }
    return `
        <div class="list-stack">
            ${dataset.ideas.ideas.map(item => `
                <div class="workflow-shell-card ${item.id === state.selectedIdeaId ? 'selected-workflow' : ''}" data-idea-id="${escapeHtml(item.id)}">
                    <div class="workflow-shell-top">
                        <div>
                            <div class="list-title">${escapeHtml(item.title)}</div>
                            <div class="list-copy">${escapeHtml(item.summary?.desired_outcome || item.messages?.[item.messages.length - 1]?.body || 'Structured idea discussion thread.')}</div>
                        </div>
                        <span class="${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
                    </div>
                    <div class="workflow-meta-grid">
                        <div><span class="meta-label">Messages</span><span>${escapeHtml(String((item.messages || []).length))}</span></div>
                        <div><span class="meta-label">Workflow link</span><span>${escapeHtml(item.linked_workflow_id || 'None')}</span></div>
                        <div><span class="meta-label">Proposal link</span><span>${escapeHtml(item.linked_proposal_id || 'None')}</span></div>
                        <div><span class="meta-label">Updated</span><span>${escapeHtml(formatRelative(item.updated_at))}</span></div>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function renderIdeaDetail(dataset) {
    const idea = selectedIdea(dataset);
    if (!idea) {
        return '<div class="empty-panel">Select an idea thread to inspect discussion, summary, and proposal draft.</div>';
    }
    const discussion = (idea.messages || []).length
        ? `<div class="list-stack compact">${idea.messages.map(message => `
            <div class="list-row soft">
                <div>
                    <div class="list-title">${escapeHtml(titleCase(message.role))}</div>
                    <div class="list-copy">${escapeHtml(message.body)}</div>
                </div>
                <div class="row-meta"><span class="meta-text">${escapeHtml(formatRelative(message.created_at))}</span></div>
            </div>
        `).join('')}</div>`
        : '<div class="empty-panel">No discussion messages recorded yet.</div>';
    const summary = idea.summary
        ? `
            <div class="session-detail-grid">
                <div><span class="meta-label">Problem</span><span>${escapeHtml(idea.summary.problem)}</span></div>
                <div><span class="meta-label">Desired outcome</span><span>${escapeHtml(idea.summary.desired_outcome)}</span></div>
                <div class="session-detail-wide"><span class="meta-label">Constraints</span><span>${escapeHtml((idea.summary.constraints || []).join(' • ') || 'None recorded')}</span></div>
                <div class="session-detail-wide"><span class="meta-label">Proposed scope</span><span>${escapeHtml((idea.summary.proposed_scope || []).join(' • ') || 'None recorded')}</span></div>
                <div class="session-detail-wide"><span class="meta-label">Notes</span><span>${escapeHtml(idea.summary.notes || 'No notes')}</span></div>
            </div>
        `
        : '<div class="empty-panel">Idea has not been finalized yet, so no summary is available.</div>';
    const draft = idea.proposal_draft
        ? `
            <div class="session-detail-grid">
                <div><span class="meta-label">Proposal title</span><span>${escapeHtml(idea.proposal_draft.title)}</span></div>
                <div><span class="meta-label">Mock-safe</span><span>${escapeHtml(idea.proposal_draft.is_mock ? 'Yes' : 'No')}</span></div>
                <div class="session-detail-wide"><span class="meta-label">Draft prompt</span><span>${escapeHtml(idea.proposal_draft.prompt)}</span></div>
                <div class="session-detail-wide"><span class="meta-label">Rationale</span><span>${escapeHtml(idea.proposal_draft.rationale)}</span></div>
            </div>
        `
        : '<div class="empty-panel">No proposal draft generated yet.</div>';
    return `
        <div class="content-grid">
            ${card('Discussion', `${discussion}${renderAddNoteForm(idea)}`, '<span class="card-hint">Structured notes for refinement, not a general-purpose chat</span>')}
            ${card('Finalized summary', summary, `<span class="card-hint">${escapeHtml(idea.status === 'finalized' || idea.status === 'converted_to_proposal' ? 'Ready for review' : 'Finalize idea to generate structured summary')}</span>`) }
            ${card('Proposal draft', draft, '<span class="card-hint">Mock-safe proposal generation hook for later workflow/proposal conversion</span>')}
            ${renderIdeaActions(idea)}
        </div>
    `;
}

function renderNewIdeaForm() {
    if (state.apiMode !== 'online') return '';
    return `
        <div class="input-panel">
            <input type="text" id="new-idea-title" class="input-field" placeholder="Idea title — what do you want to build or fix?">
            <textarea id="new-idea-note" class="input-textarea" placeholder="Initial note (optional) — context, constraints, goals..."></textarea>
            <div class="input-actions">
                <button class="btn btn-primary" type="button" id="btn-create-idea">Create idea</button>
            </div>
        </div>
    `;
}

function renderIdeaActions(idea) {
    if (!idea) return '';
    if (idea.status === 'converted_to_proposal') {
        const wfLink = idea.linked_workflow_id
            ? `<a class="nav-link" data-nav-workflow="${escapeHtml(idea.linked_workflow_id)}" href="#">${escapeHtml(idea.linked_workflow_id)}</a>`
            : 'linked';
        return `<div class="converted-notice">Converted to proposal — workflow ${wfLink}</div>`;
    }
    const canFinalize = idea.status === 'draft' || idea.status === 'discussing';
    const canConvert = idea.status === 'finalized';
    return `
        <div class="action-bar">
            ${canFinalize ? '<button class="btn" type="button" id="btn-finalize-idea">Finalize idea</button>' : ''}
            ${canConvert ? '<button class="btn btn-primary" type="button" id="btn-convert-idea">Convert to proposal</button>' : ''}
        </div>
    `;
}

function renderAddNoteForm(idea) {
    if (!idea || state.apiMode !== 'online') return '';
    if (idea.status === 'converted_to_proposal' || idea.status === 'finalized') return '';
    return `
        <div class="input-panel">
            <textarea id="idea-message-body" class="input-textarea" placeholder="Add a discussion note..."></textarea>
            <div class="input-actions">
                <button class="btn" type="button" id="btn-add-idea-note">Add note</button>
            </div>
        </div>
    `;
}

function renderIdeas(dataset) {
    return `
        ${renderNewIdeaForm()}
        <div class="content-grid two-up">
            ${card('Idea list', ideaRows(dataset), '<span class="card-hint">Draft → discussing → finalized → converted_to_proposal</span>')}
            ${card('Idea detail', renderIdeaDetail(dataset), '<span class="card-hint">Discussion, finalized summary, and proposal draft in one place</span>')}
        </div>
    `;
}

function renderProposals(dataset) {
    if (!dataset.proposals.length) {
        return card('Proposals', `<div class="empty-panel">No proposal summaries available yet. Placeholder shell remains active.</div>`);
    }
    return card('Proposals', `
        <div class="table-shell">
            <div class="table-head table-row proposal-table-row">
                <span>Proposal</span>
                <span>Files</span>
                <span>Tokens</span>
                <span>Status</span>
                <span>Action</span>
            </div>
            ${dataset.proposals.map(item => {
                const dispatchable = item.approval === 'approved' || item.approval === 'auto_approved';
                const dispatching = state.dispatchActionKey === `${item.workflowId}:${item.proposalId}`;
                return `
                <div class="table-row proposal-row proposal-table-row">
                    <div>
                        <div class="list-title">${escapeHtml(item.batch)} · ${escapeHtml(item.title)}</div>
                        <div class="list-copy">${escapeHtml(item.prompt)}</div>
                    </div>
                    <div class="file-pill-wrap">${item.files.length ? item.files.map(file => `<span class="file-pill">${escapeHtml(file)}</span>`).join('') : '<span class="meta-text">No files listed</span>'}</div>
                    <span class="meta-text">${escapeHtml(item.tokens)}</span>
                    <span class="${badgeClass(item.approval === 'auto_approved' ? 'approved' : item.approval)}">${escapeHtml(item.approval)}</span>
                    <span>${dispatchable ? `<button class="btn btn-dispatch" type="button" data-dispatch-workflow="${escapeHtml(item.workflowId)}" data-dispatch-proposal="${escapeHtml(item.proposalId)}" ${dispatching || state.apiMode !== 'online' ? 'disabled' : ''}>${dispatching ? 'Dispatching\u2026' : 'Dispatch'}</button>` : '<span class="meta-text">—</span>'}</span>
                </div>`;
            }).join('')}
        </div>
    `);
}

function approvalActionState(workflowId, proposalId, action) {
    return state.approvalActionKey === `${workflowId}:${proposalId}:${action}`;
}

function mobileApprovalCard(item) {
    const approving = approvalActionState(item.workflowId, item.proposalId, 'approved');
    const rejecting = approvalActionState(item.workflowId, item.proposalId, 'rejected');
    const busy = approving || rejecting;
    const resolved = item.approval !== 'pending';
    return `
        <article class="approval-card-mobile">
            <div class="approval-card-top">
                <div>
                    <div class="approval-eyebrow">${escapeHtml(item.workflow)}</div>
                    <div class="approval-title">${escapeHtml(item.title)}</div>
                </div>
                <span class="${badgeClass(item.approval === 'auto_approved' ? 'approved' : item.approval)}">${escapeHtml(item.approval)}</span>
            </div>
            <div class="approval-summary">${escapeHtml(item.summary)}</div>
            <div class="approval-rationale">${escapeHtml(item.note)}</div>
            <div class="approval-meta-stack">
                <span class="meta-chip">${escapeHtml(item.scope)}</span>
                <span class="meta-chip">${escapeHtml(item.age)}</span>
                <span class="meta-chip">${escapeHtml(item.risk)}</span>
            </div>
            <div class="approval-actions-mobile">
                <button class="btn btn-primary approval-action-btn" type="button" data-approval-action="approved" data-workflow-id="${escapeHtml(item.workflowId)}" data-proposal-id="${escapeHtml(item.proposalId)}" ${busy || resolved || state.apiMode !== 'online' ? 'disabled' : ''}>${approving ? 'Approving…' : 'Approve'}</button>
                <button class="btn approval-action-btn approval-action-danger" type="button" data-approval-action="rejected" data-workflow-id="${escapeHtml(item.workflowId)}" data-proposal-id="${escapeHtml(item.proposalId)}" ${busy || resolved || state.apiMode !== 'online' ? 'disabled' : ''}>${rejecting ? 'Rejecting…' : 'Reject'}</button>
                ${(item.approval === 'approved' || item.approval === 'auto_approved') ? `<button class="btn btn-dispatch" type="button" data-dispatch-workflow="${escapeHtml(item.workflowId)}" data-dispatch-proposal="${escapeHtml(item.proposalId)}" ${state.dispatchActionKey === item.workflowId + ':' + item.proposalId || state.apiMode !== 'online' ? 'disabled' : ''}>${state.dispatchActionKey === item.workflowId + ':' + item.proposalId ? 'Dispatching\u2026' : 'Dispatch'}</button>` : ''}
            </div>
        </article>
    `;
}

function approvalCards(dataset) {
    if (state.apiMode === 'loading') {
        return '<div class="empty-panel">Loading approval queue for mobile review…</div>';
    }
    if (state.apiMode !== 'online') {
        return '<div class="empty-panel">Backend unavailable. Approval actions are disabled until the orchestrator reconnects.</div>';
    }
    if (!dataset.approvals.length) {
        return '<div class="empty-panel">No pending proposals need review right now.</div>';
    }
    return `<div class="approval-card-list">${dataset.approvals.map(mobileApprovalCard).join('')}</div>`;
}

function renderApprovalPosture(dataset) {
    const pendingCount = dataset.approvals.filter(item => item.approval === 'pending').length;
    return `
        <div class="state-grid single">
            ${stateBlock('loading', 'Mobile-first review', 'Tap-friendly approval cards stay readable on phone-sized layouts and still render cleanly on desktop.')}
            ${stateBlock('empty', 'Approval safety', 'Only approval state changes are exposed here. No live apply or runtime execution controls are mounted.')}
            ${stateBlock('loading', 'Queue status', pendingCount ? `${pendingCount} pending proposal${pendingCount === 1 ? '' : 's'} ready for review.` : 'No pending proposals in the queue.')}
        </div>
    `;
}

function renderApprovals(dataset) {
    return `
        <div class="content-grid approval-mobile-layout two-up">
            ${card('Approval queue', approvalCards(dataset), '<span class="card-hint">Mobile-first review path with safe approve / reject controls</span>')}
            ${card('Approval posture', renderApprovalPosture(dataset))}
        </div>
    `;
}

function renderExperiments(dataset) {
    if (!dataset.experiments.length) {
        return card('Experiments', `<div class="empty-panel">No experiment summaries available yet. Placeholder cards remain polished and stable.</div>`);
    }
    return card('Experiments', `
        <div class="experiment-grid">
            ${dataset.experiments.map(item => `
                <article class="experiment-card">
                    <div class="operator-row">
                        <div class="list-title">${escapeHtml(item.title)}</div>
                        <span class="${badgeClass(item.outcome)}">${escapeHtml(item.outcome)}</span>
                    </div>
                    <div class="experiment-stats">
                        <div><span class="meta-label">Progress</span><span>${escapeHtml(item.progress)}</span></div>
                        <div><span class="meta-label">Tokens</span><span>${escapeHtml(item.tokens)}</span></div>
                        <div><span class="meta-label">Files</span><span>${escapeHtml(item.files)}</span></div>
                    </div>
                    <div class="list-copy">${escapeHtml(item.note)}</div>
                </article>
            `).join('')}
        </div>
    `);
}

function renderProviders(dataset) {
    return `
        <div class="content-grid two-up">
            ${card('Providers / roles', `
                <div class="provider-grid">
                    ${dataset.providers.map(item => `
                        <article class="provider-card">
                            <div class="operator-row">
                                <div>
                                    <div class="provider-role">${escapeHtml(item.role)}</div>
                                    <div class="provider-name">${escapeHtml(item.provider)}</div>
                                </div>
                                <span class="${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
                            </div>
                            <div class="provider-model">${escapeHtml(item.model)}</div>
                            <div class="list-copy">${escapeHtml(item.focus)}</div>
                        </article>
                    `).join('')}
                </div>
            `)}
            ${card('System readiness', `
                <div class="list-stack compact">
                    <div class="list-row soft"><span class="meta-label">Theme foundation</span><span>Ready</span></div>
                    <div class="list-row soft"><span class="meta-label">Read-only endpoint mode</span><span>${state.apiMode === 'online' ? 'Connected' : 'Fallback'}</span></div>
                    <div class="list-row soft"><span class="meta-label">Responsive shell</span><span>Mobile-first approvals</span></div>
                    <div class="list-row soft"><span class="meta-label">Live runtime actions</span><span>Deferred</span></div>
                </div>
            `)}
        </div>
    `;
}

function renderPage(sectionId = state.activeSection) {
    state.activeSection = sectionId;
    const section = sections.find(item => item.id === sectionId) || sections[0];
    const dataset = getDataset();
    if (!state.selectedWorkflowId && dataset.workflows.length) {
        state.selectedWorkflowId = dataset.workflows[0].id;
    }
    document.getElementById('page-title').textContent = section.label;
    renderNav(section.id);
    renderOperators();

    const page = {
        overview: renderOverview(dataset),
        workflow: renderWorkflowState(dataset),
        queue: renderQueue(dataset),
        sessions: renderSessions(dataset),
        ideas: renderIdeas(dataset),
        assistant: renderAssistant(dataset),
        handoff: renderHandoff(),
        proposals: renderProposals(dataset),
        approvals: renderApprovals(dataset),
        experiments: renderExperiments(dataset),
        providers: renderProviders(dataset),
    };

    document.getElementById('page-content').innerHTML = page[section.id];
    bindPostRenderInteractions();
}

function setApiStatus(mode, label) {
    const el = document.getElementById('api-status');
    const dot = el.querySelector('.dot');
    dot.className = `dot ${mode === 'online' ? 'online' : mode === 'loading' ? 'loading' : 'offline'}`;
    el.querySelector('.label').textContent = label;
}

async function fetchJson(url) {
    const response = await fetch(url, { method: 'GET', headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return response.json();
}

async function postJson(url, body = {}, method = 'POST') {
    const response = await fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return response.json();
}

async function updateProposalApproval(workflowId, proposalId, approval) {
    state.approvalActionKey = `${workflowId}:${proposalId}:${approval}`;
    renderPage(state.activeSection);
    try {
        await postJson(`/api/workflows/${encodeURIComponent(workflowId)}/proposals/${encodeURIComponent(proposalId)}/approve`, {
            approval,
            notes: `Updated from mobile approvals view: ${approval}`,
        });
        await loadBackendData();
    } finally {
        state.approvalActionKey = '';
        renderPage(state.activeSection);
    }
}

async function dispatchBuilderJob(workflowId, proposalId) {
    if (state.apiMode !== 'online') return;
    state.dispatchActionKey = `${workflowId}:${proposalId}`;
    renderPage(state.activeSection);
    try {
        const result = await postJson(`/api/workflows/${encodeURIComponent(workflowId)}/builder-jobs`, {
            proposal_id: proposalId,
            category: 'build',
        });
        state.lastDispatchedJobId = result?.accepted?.job_id || result?.job?.job_id || result?.job?.id || null;
        await loadBackendData();
        renderPage('queue');
    } catch {
        renderPage(state.activeSection);
    } finally {
        state.dispatchActionKey = '';
    }
}

async function assignJobToSession(sessionId, jobId) {
    if (state.apiMode !== 'online' || !sessionId || !jobId) return;
    state.assignActionKey = sessionId;
    renderPage(state.activeSection);
    try {
        await postJson(`/api/sessions/${encodeURIComponent(sessionId)}/assign`, {
            job_id: jobId,
            next_expected_action: 'Deliver prompt contract to session.',
        });
        state.selectedSessionId = sessionId;
        await loadBackendData();
        renderPage('sessions');
    } catch {
        renderPage(state.activeSection);
    } finally {
        state.assignActionKey = '';
    }
}

async function loadPromptPreview(sessionId) {
    if (state.apiMode !== 'online' || !sessionId) return;
    state.promptPreviewLoading = sessionId;
    try {
        const result = await fetchJson(`/api/sessions/${encodeURIComponent(sessionId)}/prompt-preview`);
        state.promptPreview = result;
        state.promptPreviewSessionId = sessionId;
        state.selectedSessionId = sessionId;
        renderPage('sessions');
    } catch {
        renderPage(state.activeSection);
    } finally {
        state.promptPreviewLoading = '';
    }
}

async function markPromptDelivered(sessionId) {
    if (state.apiMode !== 'online' || !sessionId) return;
    state.deliveryActionKey = sessionId;
    renderPage(state.activeSection);
    try {
        await postJson(`/api/sessions/${encodeURIComponent(sessionId)}/mark-delivered`, {
            operator: 'operator',
            note: 'Prompt delivered manually from control tower.',
        });
        state.promptPreview = null;
        state.promptPreviewSessionId = null;
        state.selectedSessionId = sessionId;
        await loadBackendData();
        renderPage('sessions');
    } catch {
        renderPage(state.activeSection);
    } finally {
        state.deliveryActionKey = '';
    }
}

function openResultForm(sessionId, session) {
    state.resultFormSessionId = state.resultFormSessionId === sessionId ? null : sessionId;
    if (state.resultFormSessionId === sessionId) {
        state.resultDraft = {
            outcome: 'success',
            summary: session?.last_result_summary || '',
            notes: '',
            nextAction: session?.next_expected_action || 'Await next assignment.',
            artifactRef: '',
        };
    }
    renderPage('sessions');
}

async function recordSessionResult(sessionId) {
    if (state.apiMode !== 'online' || !sessionId) return;
    const summaryEl = document.getElementById('result-summary');
    const notesEl = document.getElementById('result-notes');
    const nextActionEl = document.getElementById('result-next-action');
    const outcomeEl = document.getElementById('result-outcome');
    const artifactEl = document.getElementById('result-artifact');
    const summary = summaryEl?.value.trim() || '';
    if (!summary) return;
    const notes = notesEl?.value.trim() || '';
    const nextExpectedAction = nextActionEl?.value.trim() || 'Await next assignment.';
    const outcome = outcomeEl?.value || 'success';
    const artifactRef = artifactEl?.value.trim() || '';

    state.resultActionKey = sessionId;
    state.resultDraft = {
        outcome,
        summary,
        notes,
        nextAction: nextExpectedAction,
        artifactRef,
    };
    renderPage(state.activeSection);
    try {
        await postJson(`/api/sessions/${encodeURIComponent(sessionId)}/result`, {
            outcome,
            last_result_summary: summary,
            notes,
            output_ref: artifactRef,
            artifacts: artifactRef ? [artifactRef] : [],
            next_expected_action: nextExpectedAction,
            metadata: {
                manual_result_entry: true,
                artifact_reference: artifactRef,
            },
        });
        state.selectedSessionId = sessionId;
        state.resultFormSessionId = null;
        state.resultDraft = {
            outcome: 'success',
            summary: '',
            notes: '',
            nextAction: 'Await next assignment.',
            artifactRef: '',
        };
        await loadBackendData();
        renderPage('sessions');
    } catch {
        renderPage(state.activeSection);
    } finally {
        state.resultActionKey = '';
    }
}

async function createDemoWorkflow() {
    if (state.apiMode !== 'online') return;
    const result = await postJson('/api/demo/workflows', {
        context: {},
        approval_mode: 'auto_with_limits',
        workflow_mode: state.selectedWorkflowMode,
    });
    if (result && result.id) {
        state.selectedWorkflowId = result.id;
    }
    await loadBackendData();
    renderPage('workflow');
}

async function runSelectedDemo() {
    if (state.apiMode !== 'online' || !state.selectedWorkflowId) return;
    await postJson(`/api/demo/workflows/${encodeURIComponent(state.selectedWorkflowId)}/run`, {});
    await loadBackendData();
}

async function createIdea() {
    if (state.apiMode !== 'online') return;
    const titleEl = document.getElementById('new-idea-title');
    const noteEl = document.getElementById('new-idea-note');
    if (!titleEl || !titleEl.value.trim()) return;
    const result = await postJson('/api/ideas', {
        title: titleEl.value.trim(),
        initial_note: noteEl ? noteEl.value.trim() : '',
    });
    if (result && result.idea) {
        state.selectedIdeaId = result.idea.id;
    }
    await loadBackendData();
}

async function addIdeaMessage() {
    if (state.apiMode !== 'online' || !state.selectedIdeaId) return;
    const bodyEl = document.getElementById('idea-message-body');
    if (!bodyEl || !bodyEl.value.trim()) return;
    await postJson(`/api/ideas/${encodeURIComponent(state.selectedIdeaId)}/messages`, {
        body: bodyEl.value.trim(),
        role: 'user',
    });
    await loadBackendData();
}

async function finalizeIdea() {
    if (state.apiMode !== 'online' || !state.selectedIdeaId) return;
    await postJson(`/api/ideas/${encodeURIComponent(state.selectedIdeaId)}/finalize`, {
        note: '',
    });
    await loadBackendData();
}

async function convertIdea() {
    if (state.apiMode !== 'online' || !state.selectedIdeaId) return;
    const result = await postJson(`/api/ideas/${encodeURIComponent(state.selectedIdeaId)}/convert`, {
        approval_mode: 'auto_with_limits',
    });
    if (result && result.workflow) {
        state.selectedWorkflowId = result.workflow.id;
    }
    await loadBackendData();
}

async function loadBackendData() {
    state.apiMode = 'loading';
    setApiStatus('loading', 'connecting');
    renderPage(state.activeSection);

    try {
        const [health, config, workflowsPayload, queuePayload, sessionsPayload, ideasPayload] = await Promise.all([
            fetchJson('/api/health'),
            fetchJson('/api/config'),
            fetchJson('/api/workflows'),
            fetchJson('/api/builder-jobs/queue'),
            fetchJson('/api/sessions'),
            fetchJson('/api/ideas'),
        ]);
        const sessionIds = Array.isArray(sessionsPayload?.sessions)
            ? sessionsPayload.sessions.map(item => item.session_id).filter(Boolean)
            : [];
        const workflowIds = Array.isArray(workflowsPayload.workflows)
            ? workflowsPayload.workflows.map(item => item.id).filter(Boolean)
            : [];
        const [detailed, sessionDetailed] = await Promise.all([
            Promise.all(workflowIds.map(id => fetchJson(`/api/workflows/${encodeURIComponent(id)}`).catch(() => null))),
            Promise.all(sessionIds.map(id => fetchJson(`/api/sessions/${encodeURIComponent(id)}`).catch(() => null))),
        ]);

        state.health = health;
        state.config = config;
        state.workflows = detailed.filter(Boolean);
        state.queue = queuePayload;
        state.sessions = sessionsPayload;
        state.sessionDetails = Object.fromEntries(
            sessionDetailed
                .filter(Boolean)
                .map(item => [item.session.session_id, item.session])
        );
        state.ideas = ideasPayload;
        state.selectedWorkflowMode = config?.workflow_modes?.default || state.selectedWorkflowMode;
        state.apiMode = 'online';
        state.error = null;
        setApiStatus('online', 'backend live');
    } catch (error) {
        state.health = null;
        state.config = null;
        state.workflows = [];
        state.queue = null;
        state.sessions = null;
        state.ideas = null;
        state.apiMode = 'fallback';
        state.error = error;
        setApiStatus('fallback', 'mock fallback');
    }

    renderPage(state.activeSection);
}

function modeBadgeHtml(modeValue, label) {
    const cls = `mode-badge mode-${String(modeValue).replace(/\s+/g, '_').toLowerCase()}`;
    return `<span class="${cls}">${escapeHtml(label || modeValue)}</span>`;
}

function renderModeSelector() {
    const select = document.getElementById('workflow-mode-select');
    const summary = document.getElementById('hero-mode-summary');
    if (!select || !summary) return;

    const availableModes = state.config?.workflow_modes?.available || [
        { value: 'compact', label: 'Compact', summary: 'Lowest cost mode.' },
        { value: 'normal', label: 'Normal', summary: 'Balanced default mode.' },
        { value: 'rich', label: 'Rich', summary: 'Higher quality mode.' },
        { value: 'go_wild', label: 'Go Wild', summary: 'Best-results-first mode.' },
    ];

    select.innerHTML = availableModes.map(item => `
        <option value="${escapeHtml(item.value)}" ${item.value === state.selectedWorkflowMode ? 'selected' : ''}>${escapeHtml(item.label)}</option>
    `).join('');

    const active = availableModes.find(item => item.value === state.selectedWorkflowMode) || availableModes[0];
    const resolved = state.config?.workflow_modes?.resolved_defaults?.[state.selectedWorkflowMode];
    const strip = resolved ? `
        <div class="mode-comparison-strip">
            <div class="mode-dim"><span class="mode-dim-label">Cost</span><span class="mode-dim-value">${escapeHtml(resolved.budgets?.cost_intensity || '—')}</span></div>
            <div class="mode-dim"><span class="mode-dim-label">Builder tokens</span><span class="mode-dim-value">${escapeHtml(String(resolved.budgets?.builder_max_tokens ?? '—'))}</span></div>
            <div class="mode-dim"><span class="mode-dim-label">Context</span><span class="mode-dim-value">${escapeHtml(resolved.context?.context_detail || '—')}</span></div>
            <div class="mode-dim"><span class="mode-dim-label">Review</span><span class="mode-dim-value">${escapeHtml(resolved.review?.review_depth || '—')}</span></div>
            <div class="mode-dim"><span class="mode-dim-label">Fan-out</span><span class="mode-dim-value">${escapeHtml(String(resolved.parallelism?.session_fan_out ?? '—'))}</span></div>
            <div class="mode-dim"><span class="mode-dim-label">Compression</span><span class="mode-dim-value">${escapeHtml(resolved.compression?.summarization || '—')}</span></div>
        </div>` : '';
    summary.innerHTML = active
        ? `${modeBadgeHtml(active.value, active.label)}<span class="hero-mode-copy">${escapeHtml(active.summary || '')}</span>${strip}`
        : '';
}

function renderDemoActions() {
    const createBtn = document.getElementById('btn-create-demo');
    const runBtn = document.getElementById('btn-run-demo');
    if (!createBtn || !runBtn) return;
    createBtn.disabled = state.apiMode !== 'online';
    runBtn.disabled = state.apiMode !== 'online' || !state.selectedWorkflowId;
    renderModeSelector();
}

function bindModeSelection() {
    const select = document.getElementById('workflow-mode-select');
    if (!select || select.dataset.bound) return;
    select.dataset.bound = 'true';
    select.addEventListener('change', () => {
        state.selectedWorkflowMode = select.value;
        renderModeSelector();
    });
}

function bindHeroActions() {
    const createBtn = document.getElementById('btn-create-demo');
    const runBtn = document.getElementById('btn-run-demo');
    if (createBtn && !createBtn.dataset.bound) {
        createBtn.dataset.bound = 'true';
        createBtn.addEventListener('click', () => createDemoWorkflow().catch(() => {}));
    }
    if (runBtn && !runBtn.dataset.bound) {
        runBtn.dataset.bound = 'true';
        runBtn.addEventListener('click', () => runSelectedDemo().catch(() => {}));
    }
    bindModeSelection();
    renderDemoActions();
}

function bindWorkflowSelection() {
    document.querySelectorAll('[data-workflow-id]').forEach(node => {
        node.addEventListener('click', () => {
            state.selectedWorkflowId = node.dataset.workflowId;
            renderPage(state.activeSection);
        });
    });
}

function bindSessionSelection() {
    document.querySelectorAll('[data-session-id]').forEach(node => {
        node.addEventListener('click', () => {
            state.selectedSessionId = node.dataset.sessionId;
            renderPage(state.activeSection);
        });
    });
}

function bindIdeaSelection() {
    document.querySelectorAll('[data-idea-id]').forEach(node => {
        node.addEventListener('click', () => {
            state.selectedIdeaId = node.dataset.ideaId;
            renderPage(state.activeSection);
        });
    });
}

function bindApprovalActions() {
    document.querySelectorAll('[data-approval-action]').forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = 'true';
        button.addEventListener('click', () => {
            const { workflowId, proposalId, approvalAction } = button.dataset;
            if (!workflowId || !proposalId || !approvalAction) return;
            updateProposalApproval(workflowId, proposalId, approvalAction).catch(() => {});
        });
    });
}

async function changeWorkflowMode(workflowId, newMode) {
    if (state.apiMode !== 'online') return;
    try {
        const res = await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/mode`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workflow_mode: newMode }),
        });
        if (!res.ok) return;
        await loadBackendData();
    } catch { /* silent */ }
}

function bindWorkflowModeChange() {
    document.querySelectorAll('.workflow-mode-change').forEach(select => {
        if (select.dataset.bound) return;
        select.dataset.bound = 'true';
        select.addEventListener('change', (e) => {
            e.stopPropagation();
            const wfId = select.dataset.workflowId;
            if (wfId) changeWorkflowMode(wfId, select.value).catch(() => {});
        });
        select.addEventListener('click', (e) => e.stopPropagation());
    });
}

function bindIdeaActions() {
    const createBtn = document.getElementById('btn-create-idea');
    if (createBtn && !createBtn.dataset.bound) {
        createBtn.dataset.bound = 'true';
        createBtn.addEventListener('click', () => createIdea().catch(() => {}));
    }
    const noteBtn = document.getElementById('btn-add-idea-note');
    if (noteBtn && !noteBtn.dataset.bound) {
        noteBtn.dataset.bound = 'true';
        noteBtn.addEventListener('click', () => addIdeaMessage().catch(() => {}));
    }
    const finalizeBtn = document.getElementById('btn-finalize-idea');
    if (finalizeBtn && !finalizeBtn.dataset.bound) {
        finalizeBtn.dataset.bound = 'true';
        finalizeBtn.addEventListener('click', () => finalizeIdea().catch(() => {}));
    }
    const convertBtn = document.getElementById('btn-convert-idea');
    if (convertBtn && !convertBtn.dataset.bound) {
        convertBtn.dataset.bound = 'true';
        convertBtn.addEventListener('click', () => convertIdea().catch(() => {}));
    }
}

function bindNavWorkflowLinks() {
    document.querySelectorAll('[data-nav-workflow]').forEach(link => {
        if (link.dataset.bound) return;
        link.dataset.bound = 'true';
        link.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            state.selectedWorkflowId = link.dataset.navWorkflow;
            renderPage('workflow');
        });
    });
}

function bindAssignActions() {
    document.querySelectorAll('.assign-session-select').forEach(select => {
        if (select.dataset.bound) return;
        select.dataset.bound = 'true';
        select.addEventListener('change', (e) => {
            e.stopPropagation();
            const jobId = select.dataset.assignJobId;
            const sessionId = select.value;
            if (jobId && sessionId) assignJobToSession(sessionId, jobId).catch(() => {});
        });
        select.addEventListener('click', (e) => e.stopPropagation());
    });
    document.querySelectorAll('.assign-job-select').forEach(select => {
        if (select.dataset.bound) return;
        select.dataset.bound = 'true';
        select.addEventListener('change', (e) => {
            e.stopPropagation();
            const sessionId = select.dataset.assignSessionId;
            const jobId = select.value;
            if (sessionId && jobId) assignJobToSession(sessionId, jobId).catch(() => {});
        });
        select.addEventListener('click', (e) => e.stopPropagation());
    });
}

function bindPreviewActions() {
    document.querySelectorAll('[data-preview-session-id]').forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = 'true';
        button.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const sessionId = button.dataset.previewSessionId;
            if (sessionId) loadPromptPreview(sessionId).catch(() => {});
        });
    });
}

function bindDeliveryActions() {
    document.querySelectorAll('[data-deliver-session-id]').forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = 'true';
        button.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const sessionId = button.dataset.deliverSessionId;
            if (sessionId) markPromptDelivered(sessionId).catch(() => {});
        });
    });
}

function bindResultActions() {
    document.querySelectorAll('[data-open-result-session-id]').forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = 'true';
        button.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const sessionId = button.dataset.openResultSessionId;
            const session = state.sessionDetails[sessionId] || null;
            if (sessionId) openResultForm(sessionId, session);
        });
    });
    document.querySelectorAll('[data-result-save-session-id]').forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = 'true';
        button.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const sessionId = button.dataset.resultSaveSessionId;
            if (sessionId) recordSessionResult(sessionId).catch(() => {});
        });
    });
    document.querySelectorAll('[data-result-cancel-session-id]').forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = 'true';
        button.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            state.resultFormSessionId = null;
            renderPage('sessions');
        });
    });
}

function bindDispatchActions() {
    document.querySelectorAll('[data-dispatch-workflow]').forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = 'true';
        button.addEventListener('click', (e) => {
            e.stopPropagation();
            const { dispatchWorkflow, dispatchProposal } = button.dataset;
            if (!dispatchWorkflow || !dispatchProposal) return;
            dispatchBuilderJob(dispatchWorkflow, dispatchProposal).catch(() => {});
        });
    });
}

function bindPostRenderInteractions() {
    bindWorkflowSelection();
    bindSessionSelection();
    bindIdeaSelection();
    bindHeroActions();
    bindApprovalActions();
    bindDispatchActions();
    bindAssignActions();
    bindPreviewActions();
    bindDeliveryActions();
    bindResultActions();
    bindWorkflowModeChange();
    bindNavWorkflowLinks();
    bindIdeaActions();
}

renderPage('overview');
loadBackendData();

// --- ASSISTANT / MEMORY VIEWS ---

const assistantState = {
    memory: null,
    chatHistory: [],
    loading: false,
    saving: false,
    input: '',
    threads: [],
    activeThreadId: '',
    threadsLoaded: false,
    providerStatus: null,
};


async function fetchMemory() {
    if (state.apiMode !== 'online') return;
    try {
        const memRes = await fetchJson('/api/memory');
        assistantState.memory = memRes;

        await loadThreads();

        const histRes = await fetchJson('/api/assistant/history');
        if (histRes && histRes.messages) {
            assistantState.chatHistory = histRes.messages;
        }
        if (histRes && histRes.thread_id) {
            assistantState.activeThreadId = histRes.thread_id;
        }

        try {
            assistantState.providerStatus = await fetchJson('/api/assistant/provider-status');
        } catch { assistantState.providerStatus = null; }
    } catch {
        assistantState.memory = { vision: '', systems: '', status: '', decisions: '', preferences: '', known_failures: '', roadmap: '' };
        assistantState.chatHistory = [];
    }
}

async function saveMemory(key, value) {
    if (!assistantState.memory) return;
    assistantState.saving = true;
    renderPage('assistant');
    
    assistantState.memory[key] = value;
    try {
        await postJson('/api/memory', assistantState.memory, 'PUT');
    } finally {
        assistantState.saving = false;
        renderPage('assistant');
    }
}

async function sendAssistantMessage(msg) {
    if (!msg.trim() || assistantState.loading) return;
    assistantState.chatHistory.push({ role: 'user', content: msg });
    assistantState.loading = true;
    renderPage('assistant');

    try {
        const res = await postJson('/api/assistant/chat', {
            message: msg,
            history: [] // backend handles appending
        });
        if (res && res.reply) {
            assistantState.chatHistory.push({ role: 'assistant', content: res.reply });
        }
        // Update provider badge from actual response
        if (res && ('provider' in res || 'is_mock' in res)) {
            assistantState.providerStatus = {
                active_provider: res.provider || 'unknown',
                model: (assistantState.providerStatus || {}).model || '',
                is_mock: !!res.is_mock,
                reason: res.is_mock ? 'No live provider key configured' : (res.provider || '') + ' provider active',
                requested_mode: (assistantState.providerStatus || {}).requested_mode || '',
            };
        }
    } catch (e) {
        assistantState.chatHistory.push({ role: 'system', content: 'Failed to connect to assistant backend.' });
    } finally {
        assistantState.loading = false;
        renderPage('assistant');
    }
}

async function clearAssistantHistory() {
    if (state.apiMode !== 'online' || assistantState.loading) return;
    assistantState.loading = true;
    renderPage('assistant');

    try {
        await postJson('/api/assistant/history', {}, 'DELETE');
        assistantState.chatHistory = [];
    } finally {
        assistantState.loading = false;
        renderPage('assistant');
    }
}

// --- THREAD MANAGEMENT ---

async function loadThreads() {
    if (state.apiMode !== 'online') return;
    try {
        const res = await fetchJson('/api/assistant/threads');
        assistantState.threads = res.threads || [];
        assistantState.activeThreadId = res.active_thread_id || '';
        assistantState.threadsLoaded = true;
    } catch {
        assistantState.threads = [];
        assistantState.threadsLoaded = true;
    }
}

async function createThread() {
    const title = prompt('Thread title (optional):');
    if (title === null) return;
    try {
        const res = await postJson('/api/assistant/threads', { title: title.trim() });
        assistantState.activeThreadId = res.thread_id;
        assistantState.chatHistory = [];
        await loadThreads();
        renderPage('assistant');
    } catch {}
}

async function switchThread(threadId) {
    if (threadId === assistantState.activeThreadId) return;
    try {
        await postJson('/api/assistant/threads/' + threadId + '/switch', {});
        assistantState.activeThreadId = threadId;
        const hist = await fetchJson('/api/assistant/history');
        assistantState.chatHistory = hist && hist.messages ? hist.messages : [];
        await loadThreads();
        renderPage('assistant');
    } catch {}
}

async function renameThread(threadId) {
    const current = assistantState.threads.find(t => t.thread_id === threadId);
    const title = prompt('New title:', current ? current.title : '');
    if (!title || title === null) return;
    try {
        await fetch('/api/assistant/threads/' + threadId + '/rename', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: title.trim() }),
        });
        await loadThreads();
        renderPage('assistant');
    } catch {}
}

async function archiveThread(threadId) {
    if (!confirm('Archive this thread?')) return;
    try {
        await postJson('/api/assistant/threads/' + threadId + '/archive', {});
        if (threadId === assistantState.activeThreadId) {
            const hist = await fetchJson('/api/assistant/history');
            assistantState.chatHistory = hist && hist.messages ? hist.messages : [];
        }
        await loadThreads();
        renderPage('assistant');
    } catch {}
}

function bindAssistantHandlers() {
    const chatInput = document.getElementById('assistant-chat-input');
    const sendBtn = document.getElementById('btn-send-msg');
    const doSend = () => {
        if (!chatInput) return;
        const val = chatInput.value;
        chatInput.value = '';
        sendAssistantMessage(val);
    };
    if (chatInput) {
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                doSend();
            }
        });
    }
    if (sendBtn) {
        sendBtn.addEventListener('click', doSend);
    }

    const fields = ['vision', 'systems', 'status', 'decisions', 'preferences', 'known_failures', 'roadmap'];
    fields.forEach(f => {
        const btn = document.getElementById('btn-save-mem-' + f);
        const txt = document.getElementById('txt-mem-' + f);
        if (btn && txt) {
            btn.addEventListener('click', () => {
                saveMemory(f, txt.value);
            });
        }
    });
    
    const btnClear = document.getElementById('btn-clear-history');
    if (btnClear) {
        btnClear.addEventListener('click', () => {
            if (confirm('Clear assistant history?')) clearAssistantHistory();
        });
    }

    // Memory toggle
    const memToggle = document.getElementById('mem-toggle');
    const memGrid = document.getElementById('mem-grid');
    const memArrow = document.getElementById('mem-toggle-arrow');
    if (memToggle && memGrid) {
        memToggle.addEventListener('click', () => {
            const hidden = memGrid.style.display === 'none';
            memGrid.style.display = hidden ? '' : 'none';
            if (memArrow) memArrow.style.transform = hidden ? '' : 'rotate(-90deg)';
        });
    }

    // Thread management
    const btnNewThread = document.getElementById('btn-new-thread');
    if (btnNewThread) {
        btnNewThread.addEventListener('click', () => createThread());
    }

    document.querySelectorAll('.thread-chip').forEach(chip => {
        chip.addEventListener('click', (e) => {
            if (e.target.closest('.thread-chip-rename') || e.target.closest('.thread-chip-archive')) return;
            const tid = chip.dataset.threadId;
            if (tid) switchThread(tid);
        });
    });

    document.querySelectorAll('.thread-chip-rename').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            renameThread(e.currentTarget.dataset.threadId);
        });
    });

    document.querySelectorAll('.thread-chip-archive').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            archiveThread(e.currentTarget.dataset.threadId);
        });
    });

    // Bind copy buttons
    document.querySelectorAll('.btn-copy-prompt').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const content = decodeURIComponent(e.currentTarget.dataset.content);
            navigator.clipboard.writeText(content).then(() => {
                const orig = e.currentTarget.textContent;
                e.currentTarget.textContent = 'Copied!';
                setTimeout(() => e.currentTarget.textContent = orig, 1500);
            });
        });
    });

    // Bind distill-to-memory buttons
    document.querySelectorAll('.btn-distill-mem').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const content = decodeURIComponent(e.currentTarget.dataset.content);
            const btn_ = e.currentTarget;
            const sections = ['status', 'decisions', 'preferences', 'known_failures', 'roadmap', 'systems', 'vision'];
            const pick = prompt(
                'Distill to which canonical memory section?\n' +
                sections.map((s, i) => `  ${i+1}  ${s}`).join('\n') +
                '\n\nType a number 1-' + sections.length + ', or cancel.'
            );
            if (pick === null) return;
            const idx = parseInt((pick || '').trim(), 10) - 1;
            if (isNaN(idx) || idx < 0 || idx >= sections.length) {
                alert('Invalid section.');
                return;
            }
            const section = sections[idx];
            const suggested = content.length > 800 ? content.slice(0, 800) + '…' : content;
            const note = prompt(
                `Distilled note for [${section}]\n` +
                'Keep it short: decisions, constraints, or current state only.\n' +
                'Edit the text below, then press OK to append. Press Cancel to abort.',
                suggested
            );
            if (note === null || !note.trim()) return;
            const replaceAns = confirm('Replace existing [' + section + '] body?\n\nOK = replace, Cancel = append.');
            btn_.disabled = true;
            btn_.textContent = 'Saving…';
            try {
                const tid = assistantState.activeThreadId || 'assistant_distill';
                const res = await postJson('/api/memory/apply-update', {
                    patches: [{
                        section,
                        note: note.trim(),
                        replace: replaceAns,
                        source: 'thread:' + tid,
                    }],
                });
                if (res && res.memory) {
                    assistantState.memory = res.memory;
                    renderPage('assistant');
                } else {
                    btn_.textContent = 'Saved ✓';
                }
            } catch {
                btn_.textContent = 'Failed';
                btn_.disabled = false;
            }
        });
    });

    // Bind save-prompt buttons
    document.querySelectorAll('.btn-save-prompt').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const content = decodeURIComponent(e.currentTarget.dataset.content);
            const btn_ = e.currentTarget;

            // Fetch linkable objects so operator can optionally attach
            let linkables = { workflows: [], sessions: [] };
            try { linkables = await fetchJson('/api/assistant/linkable-objects'); } catch {}

            // Build a simple dialog using prompt() for label + confirm() for link choice
            const name = prompt('Label for this saved prompt (optional):');
            if (name === null) return; // operator cancelled entirely

            let linkType = '';
            let linkId = '';

            const hasObjects = linkables.workflows.length || linkables.sessions.length;
            if (hasObjects) {
                // Numbered index picker — operator types W1, S2, etc.  No hex IDs needed.
                const wfLines = linkables.workflows.map((w, i) => `  W${i+1}  ${w.label}`).join('\n');
                const sLines  = linkables.sessions.map((s, i)  => `  S${i+1}  ${s.label}`).join('\n');
                const lines   = [
                    'Attach to a record? Type W1, W2 … or S1, S2 … then press OK.',
                    'Leave blank to save standalone.',
                    '',
                    ...(wfLines ? ['WORKFLOWS:', wfLines] : []),
                    ...(sLines  ? ['SESSIONS:',  sLines]  : []),
                ].join('\n');
                const choice = prompt(lines);
                if (choice === null) return; // operator cancelled
                const key = choice.trim().toUpperCase();
                if (key) {
                    const wfMatch  = key.match(/^W(\d+)$/);
                    const sessMatch = key.match(/^S(\d+)$/);
                    if (wfMatch) {
                        const wf = linkables.workflows[parseInt(wfMatch[1], 10) - 1];
                        if (wf) { linkType = 'workflow'; linkId = wf.id; }
                    } else if (sessMatch) {
                        const sess = linkables.sessions[parseInt(sessMatch[1], 10) - 1];
                        if (sess) { linkType = 'session'; linkId = sess.id; }
                    }
                }
            }

            btn_.disabled = true;
            btn_.textContent = 'Saving…';
            try {
                const res = await postJson('/api/assistant/save-prompt', {
                    content, name: name.trim(), source_role: 'assistant',
                    link_type: linkType, link_id: linkId,
                });
                const linked = res.linked ? ` → linked to ${res.link_type} ${res.link_id.slice(0,8)}` : '';
                btn_.textContent = 'Saved ✓';
                btn_.title = `Saved as: ${res.id}${linked}`;
            } catch {
                btn_.textContent = 'Save failed';
                btn_.disabled = false;
            }
        });
    });
}

function renderThreadList() {
    if (!assistantState.threadsLoaded || assistantState.threads.length === 0) {
        return '<span style="opacity: 0.5; font-size: 11px;">No threads</span>';
    }
    return assistantState.threads.map(t => {
        const active = t.thread_id === assistantState.activeThreadId;
        const label = escapeHtml(t.title || 'Untitled');
        const count = t.message_count || 0;
        return `<div class="thread-chip ${active ? 'thread-active' : ''}" data-thread-id="${t.thread_id}" title="${label} (${count} messages)">
            <span class="thread-chip-label">${label}</span>
            <span class="thread-chip-count">${count}</span>
            <button class="thread-chip-rename" data-thread-id="${t.thread_id}" title="Rename">&#9998;</button>
            ${!active ? `<button class="thread-chip-archive" data-thread-id="${t.thread_id}" title="Archive">&times;</button>` : ''}
        </div>`;
    }).join('');
}

function renderProviderBadge() {
    const ps = assistantState.providerStatus;
    if (!ps) return '';
    if (ps.is_mock) {
        const reason = escapeHtml(ps.reason || 'no live provider');
        return `<span class="${badgeClass('fallback')}" title="${reason}" style="font-size:10px;cursor:help;">mock</span>`;
    }
    const label = escapeHtml(ps.active_provider + (ps.model ? ' / ' + ps.model : ''));
    return `<span class="${badgeClass('online')}" style="font-size:10px;">${label}</span>`;
}

function renderAssistant(dataset) {
    if (state.apiMode !== 'online') {
        return '<div class="empty-state">API offline. Cannot load Assistant.</div>';
    }

    if (!assistantState.memory) {
        fetchMemory().then(() => renderPage('assistant'));
        return '<div class="empty-state">Loading brain...</div>';
    }

    const mem = assistantState.memory;

    // Collapse consecutive mock/error messages into one entry with count
    const _msgs = [];
    let _mockRun = 0;
    assistantState.chatHistory.forEach(m => {
        const t = m.content || '';
        const isMock = t.startsWith('Mock response for role=');
        const isErr = t.startsWith('Provider error:');
        if (isMock || isErr) {
            _mockRun++;
            if (_mockRun === 1) _msgs.push({ ...m, _n: 1, _mock: isMock, _err: isErr });
            else _msgs[_msgs.length - 1]._n = _mockRun;
        } else {
            _mockRun = 0;
            _msgs.push(m);
        }
    });

    let chatHtml = _msgs.map((m, idx) => {
        const cls = m.role === 'user' ? 'chat-user' : m.role === 'assistant' ? 'chat-ai' : 'chat-sys';
        const label = m.role === 'user' ? 'Operator' : m.role === 'assistant' ? 'Brain' : 'System';
        const text = m.content || '';

        if (m._mock) {
            const n = m._n > 1 ? ` (x${m._n})` : '';
            return `<div class="chat-msg chat-mock">Mock mode${n}</div>`;
        }
        if (m._err) {
            const brief = text.replace(/^Provider error:\s*/, '').replace(/Error code: \d+ - /, '').split("'message':")[1]?.split("'")[1] || text.slice(0, 80);
            const n = m._n > 1 ? ` (x${m._n})` : '';
            return `<div class="chat-msg chat-mock">Error: ${escapeHtml(brief)}${n}</div>`;
        }

        let copyBtn = '';
        if (m.role === 'assistant') {
            const enc = encodeURIComponent(text);
            copyBtn = `<button class="btn btn-copy-prompt" style="font-size: 10px; padding: 2px 6px; float: right; margin-left: 4px;" data-content="${enc}">Copy</button><button class="btn btn-save-prompt" style="font-size: 10px; padding: 2px 6px; float: right;" data-content="${enc}">Save Prompt</button><button class="btn btn-distill-mem" style="font-size: 10px; padding: 2px 6px; float: right; margin-right: 4px;" data-content="${enc}" title="Save distilled memory update">&rarr; Memory</button>`;
        }

        return `<div class="chat-msg ${cls}">
            <div class="chat-role">${label} ${copyBtn}</div>
            <div class="chat-body">${text.replace(/\n/g, '<br>')}</div>
        </div>`;
    }).join('');

    if (assistantState.loading) {
        chatHtml += '<div class="chat-msg chat-ai"><div class="chat-role">Brain</div><div class="chat-body"><span class="dot loading"></span> Thinking...</div></div>';
    }

    if (chatHtml === '') {
        chatHtml = '<div class="empty-state">Project brain is ready. Ask a question or request a prompt.</div>';
    }

    const mkSection = (id, title, val) => `
        <div class="card" style="margin-bottom: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <div class="card-title">${title}</div>
                <button class="btn" id="btn-save-mem-${id}" ${assistantState.saving ? 'disabled':''}>Save</button>
            </div>
            <textarea id="txt-mem-${id}" style="width: 100%; min-height: 80px; background: var(--bg); color: var(--text); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px; font-family: monospace;">${val}</textarea>
        </div>
    `;
    

    // Hook post-render
    setTimeout(bindAssistantHandlers, 0);

    return `
        <style>
            .asst-layout { display: flex; flex-direction: column; gap: 12px; }
            .asst-chat { display: flex; flex-direction: column; background: var(--bg-panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; height: 46vh; min-height: 220px; max-height: 50vh; }
            .asst-chat-log { flex: 1; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; }
            .asst-chat-input-box { border-top: 1px solid var(--line); padding: 8px 12px; background: var(--bg-soft); flex-shrink: 0; }
            .asst-chat-input-row { display: flex; gap: 8px; align-items: flex-end; }
            .asst-chat-input-row textarea { flex: 1; min-height: 34px; max-height: 72px; background: var(--bg); color: var(--text); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 6px 8px; font-family: sans-serif; resize: none; font-size: 13px; }
            .asst-chat-input-row textarea:focus { border-color: var(--accent); outline: none; }
            .asst-send-btn { padding: 7px 14px; background: var(--accent); color: #fff; border: none; border-radius: var(--radius-sm); cursor: pointer; font-size: 12px; font-weight: 600; white-space: nowrap; flex-shrink: 0; }
            .asst-send-btn:hover { opacity: 0.9; }
            .asst-send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
            .chat-msg { padding: 6px 10px; border-radius: var(--radius-sm); font-size: 13px; line-height: 1.4; }
            .chat-user { background: var(--bg-soft); border-left: 3px solid var(--accent); }
            .chat-ai { background: var(--bg-soft); border-left: 3px solid var(--success); }
            .chat-ai .chat-body { max-height: 180px; overflow-y: auto; }
            .chat-sys { background: var(--bg-soft); border-left: 3px solid var(--danger); font-style: italic; opacity: 0.8; }
            .chat-mock { background: transparent; border-left: 3px solid var(--warning); opacity: 0.55; font-size: 11px; padding: 3px 10px; line-height: 1.3; }
            .chat-role { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 1px; opacity: 0.5; font-weight: 600; }
            .asst-thread-bar { display: flex; justify-content: space-between; align-items: center; padding: 6px 12px; border-bottom: 1px solid var(--line); background: var(--bg-soft); gap: 8px; min-height: 34px; }
            .asst-thread-list { display: flex; gap: 4px; overflow-x: auto; flex: 1; align-items: center; }
            .thread-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; font-size: 11px; cursor: pointer; background: var(--bg); border: 1px solid var(--line); white-space: nowrap; transition: all 0.15s; }
            .thread-chip:hover { border-color: var(--accent); }
            .thread-active { background: var(--accent); color: #fff; border-color: var(--accent); }
            .thread-chip-label { max-width: 120px; overflow: hidden; text-overflow: ellipsis; }
            .thread-chip-count { opacity: 0.6; font-size: 10px; }
            .thread-chip-rename, .thread-chip-archive { background: none; border: none; cursor: pointer; font-size: 11px; padding: 0 2px; opacity: 0.5; color: inherit; }
            .thread-chip-rename:hover, .thread-chip-archive:hover { opacity: 1; }
            .asst-mem-section { flex-shrink: 0; }
            .asst-mem-toggle { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 8px 0; user-select: none; }
            .asst-mem-toggle .eyebrow { margin: 0; }
            .asst-mem-toggle-arrow { font-size: 10px; opacity: 0.5; transition: transform 0.15s; }
            .asst-mem-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; max-height: 300px; overflow-y: auto; padding-top: 8px; }
            .asst-mem-grid .card { margin-bottom: 0; }
            .asst-mem-grid textarea { min-height: 60px; }
            .asst-mcp-banner { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 8px 10px; font-size: 11px; line-height: 1.4; border: 1px solid var(--line); border-left: 3px solid var(--accent); background: var(--bg-soft); border-radius: var(--radius-sm); opacity: 0.85; }
            .asst-mcp-banner code { background: var(--bg); padding: 0 4px; border-radius: 3px; font-size: 10px; }
            .badge-secondary { display: inline-block; padding: 1px 6px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; background: var(--accent); color: #fff; border-radius: 10px; font-weight: 600; }
        </style>
        <div class="asst-layout">
            <div class="asst-mcp-banner" title="Orchestrator exposes memory, saved prompts, handoff status, and project status as MCP tools. Claude is the intended primary chat surface; this in-app chat is a secondary/optional path.">
                <span class="badge-secondary">Secondary surface</span>
                Primary brain is <b>Claude (via MCP)</b>. This control tower exposes
                <code>get_canonical_memory</code>, <code>apply_memory_update</code>,
                <code>list_saved_prompts</code>, <code>save_prompt</code>,
                <code>update_prompt_status</code>, <code>list_linkable_objects</code>,
                <code>get_project_status</code> via
                <code>python -m backend.mcp_server</code> (stdio) or <code>--sse</code> (127.0.0.1:8101).
                The embedded chat below remains for quick local tests and observability.
            </div>
            <div class="asst-chat">
                <div class="asst-thread-bar">
                    <div class="asst-thread-list" id="thread-list">
                        ${renderThreadList()}
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        ${renderProviderBadge()}
                        <button class="btn" id="btn-new-thread" style="font-size: 11px;" title="New thread">+ New</button>
                        <button class="btn" id="btn-clear-history" style="font-size: 11px;">Clear</button>
                    </div>
                </div>
                <div class="asst-chat-log" id="assistant-chat-log">
                    ${chatHtml}
                </div>
                <div class="asst-chat-input-box">
                    <div class="asst-chat-input-row">
                        <textarea id="assistant-chat-input" placeholder="Ask the brain..." ${assistantState.loading ? 'disabled':''}></textarea>
                        <button class="asst-send-btn" id="btn-send-msg" ${assistantState.loading ? 'disabled':''}>Send</button>
                    </div>
                </div>
            </div>
            <div class="asst-mem-section">
                <div class="asst-mem-toggle" id="mem-toggle">
                    <span class="asst-mem-toggle-arrow" id="mem-toggle-arrow">&#9660;</span>
                    <span class="eyebrow">Canonical Project Memory</span>
                </div>
                <div class="asst-mem-grid" id="mem-grid">
                    ${mkSection('vision', 'Vision & Goals', mem.vision)}
                    ${mkSection('systems', 'Current Systems', mem.systems)}
                    ${mkSection('status', 'Current Status', mem.status)}
                    ${mkSection('roadmap', 'Roadmap & Priorities', mem.roadmap)}
                    ${mkSection('decisions', 'Decisions Made', mem.decisions)}
                    ${mkSection('preferences', 'Preferences & Constraints', mem.preferences)}
                    ${mkSection('known_failures', 'Known Failures', mem.known_failures)}
                </div>
            </div>
        </div>
    `;
}

// --- HANDOFF PANEL ---

const handoffState = { prompts: null, loading: false, filters: { q: '', source_role: '', link_type: '', sort: 'newest' } };

async function loadHandoffPrompts() {
    try {
        const f = handoffState.filters;
        const params = new URLSearchParams();
        if (f.q) params.set('q', f.q);
        if (f.source_role) params.set('source_role', f.source_role);
        if (f.link_type) params.set('link_type', f.link_type);
        if (f.sort && f.sort !== 'newest') params.set('sort', f.sort);
        const qs = params.toString();
        const res = await fetchJson('/api/assistant/saved-prompts' + (qs ? '?' + qs : ''));
        handoffState.prompts = res.prompts || [];
    } catch {
        handoffState.prompts = [];
    }
}

async function setHandoffStatus(promptId, newStatus) {
    handoffState.loading = true;
    renderPage('handoff');
    try {
        await fetch('/api/assistant/saved-prompts/' + encodeURIComponent(promptId) + '/status', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus }),
        });
        await loadHandoffPrompts();
    } finally {
        handoffState.loading = false;
        renderPage('handoff');
    }
}

function bindHandoffHandlers() {
    document.querySelectorAll('.ho-copy').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const body = decodeURIComponent(e.currentTarget.dataset.body);
            navigator.clipboard.writeText(body).then(() => {
                const orig = e.currentTarget.textContent;
                e.currentTarget.textContent = 'Copied!';
                setTimeout(() => e.currentTarget.textContent = orig, 1500);
            });
        });
    });
    document.querySelectorAll('.ho-status').forEach(btn => {
        btn.addEventListener('click', (e) => {
            setHandoffStatus(e.currentTarget.dataset.id, e.currentTarget.dataset.status);
        });
    });
    // Search & filter controls
    let searchTimer = null;
    const searchInput = document.getElementById('ho-search');
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                handoffState.filters.q = searchInput.value.trim();
                handoffState.prompts = null;
                renderPage('handoff');
            }, 300);
        });
    }
    const roleSelect = document.getElementById('ho-filter-role');
    if (roleSelect) {
        roleSelect.addEventListener('change', () => {
            handoffState.filters.source_role = roleSelect.value;
            handoffState.prompts = null;
            renderPage('handoff');
        });
    }
    const linkSelect = document.getElementById('ho-filter-link');
    if (linkSelect) {
        linkSelect.addEventListener('change', () => {
            handoffState.filters.link_type = linkSelect.value;
            handoffState.prompts = null;
            renderPage('handoff');
        });
    }
    const sortSelect = document.getElementById('ho-filter-sort');
    if (sortSelect) {
        sortSelect.addEventListener('change', () => {
            handoffState.filters.sort = sortSelect.value;
            handoffState.prompts = null;
            renderPage('handoff');
        });
    }
}

function renderHandoff() {
    if (state.apiMode !== 'online') {
        return '<div class="empty-state">API offline.</div>';
    }
    if (!handoffState.prompts) {
        loadHandoffPrompts().then(() => renderPage('handoff'));
        return '<div class="empty-state">Loading handoff prompts…</div>';
    }

    const prompts = handoffState.prompts;
    if (!prompts.length) {
        return '<div class="empty-state">No saved prompts yet. Use the Assistant Brain to generate and save prompts.</div>';
    }

    const groups = { workflow: [], session: [], standalone: [] };
    prompts.forEach(p => {
        if (p.link_type === 'workflow') groups.workflow.push(p);
        else if (p.link_type === 'session') groups.session.push(p);
        else groups.standalone.push(p);
    });

    const statusBadge = (s) => {
        const colors = { drafted: 'var(--text-2)', ready_to_send: 'var(--primary)', sent_manually: 'var(--success)' };
        const labels = { drafted: 'Drafted', ready_to_send: 'Ready', sent_manually: 'Sent' };
        return `<span style="font-size:11px; padding:2px 8px; border-radius:10px; background:${colors[s] || 'var(--text-2)'}22; color:${colors[s] || 'var(--text-2)'}; font-weight:600;">${labels[s] || s}</span>`;
    };

    const statusButtons = (p) => {
        const all = [
            { val: 'drafted', lbl: 'Draft' },
            { val: 'ready_to_send', lbl: 'Ready' },
            { val: 'sent_manually', lbl: 'Sent' },
        ];
        return all.map(s =>
            `<button class="btn ho-status" data-id="${p.id}" data-status="${s.val}" style="font-size:10px; padding:1px 6px; opacity:${p.handoff_status === s.val ? '1' : '0.5'};" ${p.handoff_status === s.val ? 'disabled' : ''}>${s.lbl}</button>`
        ).join(' ');
    };

    const renderCard = (p) => {
        const ts = new Date(p.created_at).toLocaleString();
        const link = p.link_type ? `<span style="opacity:0.6; font-size:11px;">→ ${p.link_type} ${(p.link_id || '').slice(0,8)}</span>` : '';
        const enc = encodeURIComponent(p.body);
        return `
            <div class="card" style="margin-bottom:var(--space-3);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:var(--space-2);">
                    <div>
                        <strong>${p.name}</strong> ${link}
                        <div style="font-size:11px; opacity:0.5;">${ts} · ${p.source_role || 'assistant'}</div>
                    </div>
                    <div style="display:flex; gap:4px; align-items:center;">
                        ${statusBadge(p.handoff_status)}
                        <button class="btn ho-copy" data-body="${enc}" style="font-size:11px; padding:2px 8px;">Copy</button>
                    </div>
                </div>
                <pre style="background:var(--bg-1); padding:var(--space-2); border-radius:var(--radius); font-size:12px; white-space:pre-wrap; max-height:200px; overflow-y:auto; margin:0 0 var(--space-2) 0;">${p.body.replace(/</g,'&lt;')}</pre>
                <div style="display:flex; gap:4px; align-items:center;">
                    <span style="font-size:11px; opacity:0.5; margin-right:4px;">Status:</span> ${statusButtons(p)}
                </div>
            </div>
        `;
    };

    const renderGroup = (title, items) => {
        if (!items.length) return '';
        return `
            <div style="margin-bottom:var(--space-4);">
                <div class="eyebrow" style="margin-bottom:var(--space-2);">${title} (${items.length})</div>
                ${items.map(renderCard).join('')}
            </div>
        `;
    };

    const f = handoffState.filters;
    const filterBar = `
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:var(--space-3); padding:var(--space-2); background:var(--bg-1); border-radius:var(--radius); border:1px solid var(--border-color);">
            <input id="ho-search" type="text" placeholder="Search by name or content…" value="${(f.q || '').replace(/"/g, '&quot;')}"
                style="flex:1; min-width:180px; padding:4px 8px; border:1px solid var(--border-color); border-radius:var(--radius); background:var(--bg-0); color:var(--text-1); font-size:12px;" />
            <select id="ho-filter-role" style="padding:4px 6px; border:1px solid var(--border-color); border-radius:var(--radius); background:var(--bg-0); color:var(--text-1); font-size:12px;">
                <option value="">All roles</option>
                <option value="assistant" ${f.source_role==='assistant'?'selected':''}>assistant</option>
                <option value="builder" ${f.source_role==='builder'?'selected':''}>builder</option>
                <option value="reviewer" ${f.source_role==='reviewer'?'selected':''}>reviewer</option>
                <option value="planner" ${f.source_role==='planner'?'selected':''}>planner</option>
            </select>
            <select id="ho-filter-link" style="padding:4px 6px; border:1px solid var(--border-color); border-radius:var(--radius); background:var(--bg-0); color:var(--text-1); font-size:12px;">
                <option value="">All links</option>
                <option value="workflow" ${f.link_type==='workflow'?'selected':''}>Workflow</option>
                <option value="session" ${f.link_type==='session'?'selected':''}>Session</option>
            </select>
            <select id="ho-filter-sort" style="padding:4px 6px; border:1px solid var(--border-color); border-radius:var(--radius); background:var(--bg-0); color:var(--text-1); font-size:12px;">
                <option value="newest" ${f.sort==='newest'?'selected':''}>Newest first</option>
                <option value="oldest" ${f.sort==='oldest'?'selected':''}>Oldest first</option>
                <option value="name" ${f.sort==='name'?'selected':''}>By name</option>
            </select>
            <span style="font-size:11px; opacity:0.5;">${prompts.length} prompt${prompts.length !== 1 ? 's' : ''}</span>
        </div>
    `;

    setTimeout(bindHandoffHandlers, 0);

    // When filters are active, show flat list (server already filtered); otherwise group
    const hasActiveFilter = f.q || f.source_role || f.link_type;
    let promptsHtml;
    if (hasActiveFilter) {
        promptsHtml = prompts.length
            ? prompts.map(renderCard).join('')
            : '<div class="empty-state">No prompts match the current filters.</div>';
    } else {
        promptsHtml = [
            renderGroup('Linked to Workflows', groups.workflow),
            renderGroup('Linked to Sessions', groups.session),
            renderGroup('Standalone', groups.standalone),
        ].join('');
    }

    return `
        <div style="max-width:900px;">
            <div style="margin-bottom:var(--space-4);">
                <div class="eyebrow">Claude Handoff Queue</div>
                <p style="font-size:13px; opacity:0.6; margin:var(--space-1) 0 0 0;">
                    Saved prompts ready for manual delivery. Copy → paste into Claude session.
                </p>
            </div>
            ${filterBar}
            ${promptsHtml}
        </div>
    `;
}
