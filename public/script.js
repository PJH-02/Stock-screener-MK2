// Stock Screener UI Script

class ScreenerUI {
    constructor() {
        this.data = null;
        this.init();
    }

    async init() {
        this.setupTabs();
        await this.loadData();
        this.renderAll();
    }

    setupTabs() {
        const tabButtons = document.querySelectorAll('.tab-button');
        tabButtons.forEach(button => {
            button.addEventListener('click', () => {
                const tabName = button.dataset.tab;
                this.switchTab(tabName);
            });
        });
    }

    switchTab(tabName) {
        // Update buttons
        document.querySelectorAll('.tab-button').forEach(btn => {
            btn.classList.remove('active');
        });
        document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');

        // Update content
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.remove('active');
        });
        document.getElementById(`${tabName}Tab`).classList.add('active');
    }

    async loadData() {
        try {
            const response = await fetch('../results/screener_results.json');
            if (!response.ok) {
                throw new Error('Failed to load data');
            }
            this.data = await response.json();
        } catch (error) {
            console.error('Error loading data:', error);
            this.showError();
        }
    }

    renderAll() {
        if (!this.data) {
            this.showError();
            return;
        }

        this.updateLastUpdated();
        this.renderCanslTable();
        this.renderBuySignalsTable();
        this.renderExitSignalsTable();
        this.updateCounts();
    }

    updateLastUpdated() {
        const elem = document.getElementById('lastUpdated');
        elem.textContent = `Last Updated: ${this.data.last_updated}`;
    }

    updateCounts() {
        document.getElementById('canslCount').textContent = this.data.cansl_passed.length;
        
        const buySignals = this.data.turtle_signals.filter(s => 
            s.Turtle_Signal === 'S1_Buy' || s.Turtle_Signal === 'S2_Buy'
        );
        document.getElementById('buyCount').textContent = buySignals.length;
        
        const exitSignals = this.data.turtle_signals.filter(s => 
            s.Turtle_Signal === 'S1_Exit' || s.Turtle_Signal === 'S2_Exit'
        );
        document.getElementById('exitCount').textContent = exitSignals.length;
    }

    renderCanslTable() {
        const tbody = document.getElementById('canslTableBody');
        
        if (this.data.cansl_passed.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="no-data">No stocks passed CANSL criteria</td></tr>';
            return;
        }

        const rows = this.data.cansl_passed.map(stock => `
            <tr>
                <td><strong>${this.escapeHtml(stock.Ticker)}</strong></td>
                <td>${this.escapeHtml(stock.CompanyName)}</td>
                <td>${this.formatPrice(stock.ClosePrice)}</td>
                </td>
            </tr>
        `).join('');

        tbody.innerHTML = rows;
    }

    renderBuySignalsTable() {
        const tbody = document.getElementById('buyTableBody');
        
        const buySignals = this.data.turtle_signals.filter(s => 
            s.Turtle_Signal === 'S1_Buy' || s.Turtle_Signal === 'S2_Buy'
        );

        if (buySignals.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="no-data">No buy signals detected</td></tr>';
            return;
        }

        const rows = buySignals.map(stock => `
            <tr>
                <td><strong>${this.escapeHtml(stock.Ticker)}</strong></td>
                <td>${this.escapeHtml(stock.CompanyName)}</td>
                <td>${this.formatPrice(stock.ClosePrice)}</td>
                <td><span class="signal-badge signal-${stock.Turtle_Signal.toLowerCase().replace('_', '-')}">${stock.Turtle_Signal}</span></td>
                <td><span class="score-badge">${stock.CANSLIM_Score}</span>
                </td>
                </tr>
        `).join('');

        tbody.innerHTML = rows;
    }
}

