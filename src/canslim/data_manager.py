import os
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta
from opendartreader import OpenDartReader
from ..utils import setup_logger, rate_limited

logger = setup_logger('data_manager')

class DataManager:
    """Manages data fetching from pykrx and DART API."""
    
    def __init__(self):
        self.dart_api_key = os.environ.get('DART_API_KEY')
        self.dart = None
        if self.dart_api_key:
            self.dart = OpenDartReader(self.dart_api_key)
        else:
            logger.warning("DART API key not found. Financial data will be unavailable.")
        
        self.today = datetime.now().strftime('%Y%m%d')
        self.start_date = (datetime.now() - timedelta(days=730)).strftime('%Y%m%d')
    
    def get_universe(self):
        """Get KOSPI 200 and KOSDAQ 150 stock tickers."""
        logger.info("Fetching stock universe (KOSPI 200 + KOSDAQ 150)...")
        
        try:
            kospi200 = stock.get_index_portfolio_deposit_file("1028", self.today)
            kosdaq150 = stock.get_index_portfolio_deposit_file("2203", self.today)
            
            # Combine tickers
            all_tickers = list(set(list(kospi200) + list(kosdaq150)))
            
            # Filter out preferred stocks and non-common stocks
            common_stocks = []
            for ticker in all_tickers:
                try:
                    # Check if it's a common stock (exclude preferred stocks ending with '0')
                    if not ticker.endswith('0'):
                        common_stocks.append(ticker)
                except:
                    continue
            
            logger.info(f"Found {len(common_stocks)} common stocks in universe")
            return common_stocks
            
        except Exception as e:
            logger.error(f"Error fetching universe: {e}")
            return []
    
    @rate_limited(calls_per_second=2)
    def get_ohlcv(self, ticker, days=400):
        """Get OHLCV data for a ticker."""
        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
            df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)
            return df
        except Exception as e:
            logger.debug(f"Error fetching OHLCV for {ticker}: {e}")
            return pd.DataFrame()
    
    def get_company_name(self, ticker):
        """Get company name for a ticker."""
        try:
            return stock.get_market_ticker_name(ticker)
        except:
            return ticker
    
    @rate_limited(calls_per_second=1)
    def get_financial_statements(self, ticker):
        """Get financial statements from DART for a company."""
        if not self.dart:
            return None
        
        try:
            # Get the company's corporate code
            corp_list = self.dart.list()
            corp_info = corp_list[corp_list['stock_code'] == ticker]
            
            if corp_info.empty:
                return None
            
            corp_code = corp_info.iloc[0]['corp_code']
            
            # Get financial statements (most recent 4 years)
            current_year = datetime.now().year
            fs_data = []
            
            for year in range(current_year - 3, current_year + 1):
                try:
                    # Try to get annual report
                    fs = self.dart.finstate_all(corp_code, year, reprt_code='11011')
                    if fs is not None and not fs.empty:
                        fs_data.append(fs)
                except:
                    continue
            
            if fs_data:
                return pd.concat(fs_data, ignore_index=True)
            return None
            
        except Exception as e:
            logger.debug(f"Error fetching financials for {ticker}: {e}")
            return None
    
    def get_market_data(self, ticker):
        """Get comprehensive market data for a stock."""
        try:
            ohlcv = self.get_ohlcv(ticker)
            if ohlcv.empty:
                return None
            
            # Get fundamental data if available
            fundamentals = stock.get_market_fundamental_by_ticker(self.today, market="ALL")
            
            data = {
                'ticker': ticker,
                'name': self.get_company_name(ticker),
                'ohlcv': ohlcv,
                'close_price': ohlcv['종가'].iloc[-1] if not ohlcv.empty else None
            }
            
            if ticker in fundamentals.index:
                data['fundamentals'] = fundamentals.loc[ticker]
            
            return data
            
        except Exception as e:
            logger.debug(f"Error fetching market data for {ticker}: {e}")
            return None
