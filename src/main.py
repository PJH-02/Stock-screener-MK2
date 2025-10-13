#!/usr/bin/env python3
"""
CANSLIM + Turtle Trading Stock Screener for Korean Market
Main execution script
"""

import json
import os
from datetime import datetime
from pathlib import Path

from canslim import (
    DataManager,
    EarningsAnalyzer,
    NewnessAnalyzer,
    SupplyAnalyzer,
    LeadershipAnalyzer
)
from turtle import TurtleSignalGenerator
from utils import setup_logger

logger = setup_logger('main')

class StockScreener:
    """Main screener orchestrator."""
    
    def __init__(self):
        logger.info("Initializing Stock Screener...")
        self.data_manager = DataManager()
        self.earnings_analyzer = EarningsAnalyzer(self.data_manager)
        self.newness_analyzer = NewnessAnalyzer()
        self.supply_analyzer = SupplyAnalyzer()
        self.leadership_analyzer = LeadershipAnalyzer(self.data_manager)
        self.turtle_generator = TurtleSignalGenerator()
        
        # Check if L criterion is available
        self.use_l_criterion = self.leadership_analyzer.is_available()
        if not self.use_l_criterion:
            logger.info("L (Leadership) criterion excluded - sector data unavailable")
    
    def screen_stock(self, ticker):
        """
        Screen a single stock through all CANSLIM criteria.
        
        Returns: dict with screening results or None if stock fails
        """
        try:
            # Get market data
            market_data = self.data_manager.get_market_data(ticker)
            if not market_data or market_data['ohlcv'].empty:
                return None
            
            ohlcv = market_data['ohlcv']
            company_name = market_data['name']
            close_price = market_data['close_price']
            
            # Initialize result
            result = {
                'ticker': ticker,
                'company_name': company_name,
                'close_price': close_price,
                'canslim_score': 0,
                'criteria': {}
            }
            
            # Get financial data for C and A criteria
            financial_data = self.data_manager.get_financial_statements(ticker)
            
            # Check C - Current Earnings
            c_pass, c_details = self.earnings_analyzer.check_c_criterion(ticker, financial_data)
            result['criteria']['C'] = {'pass': c_pass, 'details': c_details}
            if c_pass:
                result['canslim_score'] += 1
            
            # Check A - Annual Earnings
            a_pass, a_details = self.earnings_analyzer.check_a_criterion(ticker, financial_data)
            result['criteria']['A'] = {'pass': a_pass, 'details': a_details}
            if a_pass:
                result['canslim_score'] += 1
            
            # Check N - Newness
            n_pass, n_details = self.newness_analyzer.check_n_criterion(ticker, ohlcv)
            result['criteria']['N'] = {'pass': n_pass, 'details': n_details}
            if n_pass:
                result['canslim_score'] += 1
            
            # Check S - Supply and Demand (Mandatory)
            s_pass, s_details = self.supply_analyzer.check_s_criterion(ticker, ohlcv)
            result['criteria']['S'] = {'pass': s_pass, 'details': s_details}
            if s_pass:
                result['canslim_score'] += 1
            
            # Check L - Leadership (if available)
            if self.use_l_criterion:
                l_pass, l_details = self.leadership_analyzer.check_l_criterion(ticker, ohlcv, None)
                result['criteria']['L'] = {'pass': l_pass, 'details': l_details}
                if l_pass:
                    result['canslim_score'] += 1
            
            # Determine if stock passes all required criteria
            required_criteria = ['C', 'A', 'N', 'S']
            if self.use_l_criterion:
                required_criteria.append('L')
            
            all_pass = all(result['criteria'][c]['pass'] for c in required_criteria)
            result['cansl_pass'] = all_pass
            
            # If stock passes CANSL, check Turtle signals
            if all_pass:
                turtle_signals = self.turtle_generator.generate_signals(ticker, ohlcv)
                result['turtle_signals'] = turtle_signals
            else:
                result['turtle_signals'] = []
            
            return result
            
        except Exception as e:
            logger.error(f"Error screening {ticker}: {e}")
            return None
    
    def run(self):
        """Execute the full screening process."""
        logger.info("=" * 60)
        logger.info("Starting CANSLIM + Turtle Trading Screener")
        logger.info("=" * 60)
        
        # Get stock universe
        tickers = self.data_manager.get_universe()
        logger.info(f"Screening {len(tickers)} stocks...")
        
        # Screen all stocks
        cansl_passed = []
        turtle_signals = []
        
        for idx, ticker in enumerate(tickers, 1):
            if idx % 10 == 0:
                logger.info(f"Progress: {idx}/{len(tickers)} stocks processed")
            
            result = self.screen_stock(ticker)
            
            if result and result['cansl_pass']:
                # Stock passed all CANSL criteria
                cansl_stock = {
                    'Ticker': result['ticker'],
                    'CompanyName': result['company_name'],
                    'ClosePrice': int(result['close_price']),
                    'CANSLIM_Score': result['canslim_score']
                }
                cansl_passed.append(cansl_stock)
                
                # Check for Turtle signals
                if result['turtle_signals']:
                    for signal in result['turtle_signals']:
                        turtle_signals.append({
                            'Ticker': result['ticker'],
                            'CompanyName': result['company_name'],
                            'ClosePrice': int(result['close_price']),
                            'CANSLIM_Score': result['canslim_score'],
                            'Turtle_Signal': signal
                        })
        
        # Prepare output
        output = {
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S KST'),
            'cansl_passed': cansl_passed,
            'turtle_signals': turtle_signals
        }
        
        # Save results
        self.save_results(output)
        
        # Log summary
        logger.info("=" * 60)
        logger.info("Screening Complete!")
        logger.info(f"CANSL Passed: {len(cansl_passed)} stocks")
        logger.info(f"Turtle Signals: {len(turtle_signals)} signals")
        logger.info("=" * 60)
        
        return output
    
    def save_results(self, output):
        """Save screening results to JSON file."""
        results_dir = Path('results')
        results_dir.mkdir(exist_ok=True)
        
        output_file = results_dir / 'screener_results.json'
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Results saved to {output_file}")


def main():
    """Main entry point."""
    try:
        screener = StockScreener()
        screener.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == '__main__':
    main()