class ScreenerUI {
    constructor() {
        this.data = null;
        this.activeMarket = null;
        this.activeTab = 'pass';
        this.init();
    }

    async init() {
        this.setupViewTabs();
        await this.loadData();
        if (!this.data) return;
        this.activeMarket = Object.keys(this.data.markets || {})[0] || null;
        this.renderAll();
    }

    setupViewTabs() {
        document.querySelectorAll('.tab-button').forEach((button) => {
            button.addEventListener('click', () => {
                this.activeTab = button.dataset.tab;
                document.querySelectorAll('.tab-button').forEach((btn) => btn.classList.remove('active'));
                button.classList.add('active');
                document.querySelectorAll('.tab-content').forEach((content) => content.classList.remove('active'));
                document.getElementById(`${this.activeTab}Tab`).classList.add('active');
            });
        });
    }

    async loadData() {
        try {
            const response = await fetch('results/screener_results.json', { cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            this.data = await response.json();
        } catch (error) {
            this.showFatalError(`Failed to load screener results: ${error.message}`);
        }
    }

    renderAll() {
        this.updateSummary();
        this.renderMarketTabs();
        this.renderMarketSummary();
        this.renderTables();
        this.renderQuality();
        this.updateCounts();
    }

    updateSummary() {
        const summary = this.data.summary || {};
        document.getElementById('lastUpdated').textContent = this.formatTimestamp(this.data.generated_at || this.data.last_updated);
        document.getElementById('totalUniverse').textContent = this.formatNumber(summary.total_universe || 0);
        document.getElementById('totalEvaluated').textContent = this.formatNumber(summary.total_evaluated || 0);
        document.getElementById('totalPassed').textContent = this.formatNumber(summary.total_canslim_passed || 0);
        document.getElementById('totalSignals').textContent = this.formatNumber(summary.total_turtle_signals || 0);
    }

    renderMarketTabs() {
        const container = document.getElementById('marketTabs');
        const markets = Object.keys(this.data.markets || {});
        container.innerHTML = markets.map((market) => `
            <button class="${market === this.activeMarket ? 'active' : ''}" data-market="${this.escapeHtml(market)}">
                ${this.escapeHtml(market)}
            </button>
        `).join('');
        container.querySelectorAll('button').forEach((button) => {
            button.addEventListener('click', () => {
                this.activeMarket = button.dataset.market;
                this.renderAll();
            });
        });
    }

    renderMarketSummary() {
        const market = this.currentMarket();
        const elem = document.getElementById('marketSummary');
        if (!market) {
            elem.innerHTML = '<div class="empty">No market data available.</div>';
            return;
        }
        elem.innerHTML = `
            <div>
                <strong>${this.escapeHtml(market.market_name || this.activeMarket)}</strong>
                <span>${this.formatNumber(market.evaluated_count || 0)} / ${this.formatNumber(market.universe_count || 0)} evaluated</span>
            </div>
            <div>
                <span>CANSLIM ${this.formatNumber((market.canslim_passed || []).length)}</span>
                <span>Turtle ${this.formatNumber((market.turtle_signals || []).length)}</span>
            </div>
        `;
    }

    renderTables() {
        const market = this.currentMarket() || {};
        const signals = market.turtle_signals || [];
        this.renderStockRows('passTableBody', market.canslim_passed || [], true, 7, 'No CANSLIM pass results for this market.');
        this.renderSignalRows(
            'buyTableBody',
            signals.filter((stock) => stock.Turtle_Signal === 'S1_Buy' || stock.Turtle_Signal === 'S2_Buy'),
            'No buy signals for this market.'
        );
        this.renderSignalRows(
            'exitTableBody',
            signals.filter((stock) => stock.Turtle_Signal === 'S1_Exit' || stock.Turtle_Signal === 'S2_Exit'),
            'No exit signals for this market.'
        );
        this.renderStockRows('topTableBody', market.top_candidates || [], true, 7, 'No candidates available.');
    }

    renderStockRows(bodyId, stocks, includeCriteria, colspan, emptyText) {
        const tbody = document.getElementById(bodyId);
        if (!stocks.length) {
            tbody.innerHTML = `<tr><td colspan="${colspan}" class="empty">${this.escapeHtml(emptyText)}</td></tr>`;
            return;
        }
        tbody.innerHTML = stocks.map((stock) => `
            <tr>
                <td>${this.escapeHtml(stock.Market)}</td>
                <td><strong>${this.escapeHtml(stock.Ticker)}</strong></td>
                <td>${this.escapeHtml(stock.CompanyName)}</td>
                <td>${this.formatPrice(stock.ClosePrice, stock.Currency)}</td>
                <td><span class="score-badge">${this.escapeHtml(stock.CANSLIM_Score)}</span></td>
                <td>${stock.RS_Percentile == null ? '-' : this.escapeHtml(stock.RS_Percentile)}</td>
                <td>${includeCriteria ? this.criteriaChips(stock.Criteria || {}) : ''}</td>
            </tr>
        `).join('');
    }

    renderSignalRows(bodyId, stocks, emptyText) {
        const tbody = document.getElementById(bodyId);
        if (!stocks.length) {
            tbody.innerHTML = `<tr><td colspan="6" class="empty">${this.escapeHtml(emptyText)}</td></tr>`;
            return;
        }
        tbody.innerHTML = stocks.map((stock) => `
            <tr>
                <td>${this.escapeHtml(stock.Market)}</td>
                <td><strong>${this.escapeHtml(stock.Ticker)}</strong></td>
                <td>${this.escapeHtml(stock.CompanyName)}</td>
                <td>${this.formatPrice(stock.ClosePrice, stock.Currency)}</td>
                <td><span class="signal-badge ${this.signalClass(stock.Turtle_Signal)}">${this.escapeHtml(stock.Turtle_Signal)}</span></td>
                <td><span class="score-badge">${this.escapeHtml(stock.CANSLIM_Score)}</span></td>
            </tr>
        `).join('');
    }

    renderQuality() {
        const market = this.currentMarket() || {};
        const alerts = (market.data_quality && market.data_quality.alerts) || [];
        const qualityList = document.getElementById('qualityList');
        qualityList.innerHTML = alerts.length
            ? alerts.map((alert) => `<div class="alert">${this.escapeHtml(alert)}</div>`).join('')
            : '<div class="ok">No data-quality alerts for this market.</div>';

        const failReasons = (market.fail_reasons && market.fail_reasons.by_criterion) || {};
        const rows = Object.keys(failReasons).map((criterion) => `
            <tr>
                <td>${this.escapeHtml(criterion)}</td>
                <td>${this.formatNumber(failReasons[criterion])}</td>
            </tr>
        `).join('');
        document.getElementById('failReasonBody').innerHTML = rows || '<tr><td colspan="2" class="empty">No failure summary.</td></tr>';
    }

    updateCounts() {
        const market = this.currentMarket() || {};
        const signals = market.turtle_signals || [];
        document.getElementById('passCount').textContent = (market.canslim_passed || []).length;
        document.getElementById('buyCount').textContent = signals.filter((stock) => stock.Turtle_Signal === 'S1_Buy' || stock.Turtle_Signal === 'S2_Buy').length;
        document.getElementById('exitCount').textContent = signals.filter((stock) => stock.Turtle_Signal === 'S1_Exit' || stock.Turtle_Signal === 'S2_Exit').length;
        document.getElementById('topCount').textContent = (market.top_candidates || []).length;
        document.getElementById('qualityCount').textContent = ((market.data_quality && market.data_quality.alerts) || []).length;
    }

    currentMarket() {
        return this.data && this.data.markets ? this.data.markets[this.activeMarket] : null;
    }

    criteriaChips(criteria) {
        return ['C', 'A', 'N', 'S', 'L'].map((key) => {
            const passed = criteria[key] && criteria[key].pass;
            return `<span class="criterion ${passed ? 'pass' : 'fail'}">${key}</span>`;
        }).join('');
    }

    signalClass(signal) {
        return String(signal || '').toLowerCase().replace('_', '-');
    }

    formatTimestamp(value) {
        if (!value) return 'Not generated';
        return String(value).replace('T', ' ');
    }

    formatPrice(value, currency) {
        if (value == null) return '-';
        const options = currency === 'KRW'
            ? { maximumFractionDigits: 0 }
            : { minimumFractionDigits: 2, maximumFractionDigits: 2 };
        return `${new Intl.NumberFormat(undefined, options).format(value)} ${this.escapeHtml(currency || '')}`;
    }

    formatNumber(value) {
        return new Intl.NumberFormat().format(value || 0);
    }

    escapeHtml(value) {
        return String(value == null ? '' : value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    showFatalError(message) {
        document.getElementById('lastUpdated').textContent = 'Load failed';
        document.querySelector('main').innerHTML = `<div class="fatal">${this.escapeHtml(message)}</div>`;
    }
}

document.addEventListener('DOMContentLoaded', () => new ScreenerUI());
