import os
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta
import OpenDartReader
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils import setup_logger, rate_limited

logger = setup_logger('data_manager')

class DataManager:
    """Manages data fetching from pykrx and DART API."""
    
    def __init__(self):
        self.dart_api_key = os.environ.get('DART_API_KEY')
        self.dart = None
        if self.dart_api_key:
            # OpenDartReader는 클래스가 아닌 함수 모듈이므로 직접 사용
            self.dart_api_key_value = self.dart_api_key
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
        
        # Combine tickers and remove duplicates
        all_tickers = list(set(list(kospi200) + list(kosdaq150)))
        
        logger.info(f"Found {len(all_tickers)} stocks in universe (including preferred stocks)")
        
        # Preferred stock filtering can be done later if needed
        return all_tickers
        
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
        if not self.dart_api_key:
            return None
        
        try:
            # Get the company's corporate code using OpenDartReader module functions
            corp_list = OpenDartReader.company_by_name(ticker)
            
            if corp_list is None or len(corp_list) == 0:
                return None
            
            # If corp_list is a list or DataFrame, get the first corp_code
            if isinstance(corp_list, pd.DataFrame):
                if corp_list.empty:
                    return None
                corp_code = corp_list.iloc[0]['corp_code']
            elif isinstance(corp_list, list) and len(corp_list) > 0:
                corp_code = corp_list[0].get('corp_code')
            else:
                return None
            
            # Get financial statements (most recent 4 years)
            current_year = datetime.now().year
            fs_data = []
            
            for year in range(current_year - 3, current_year + 1):
                try:
                    # Use OpenDartReader module function directly
                    fs = OpenDartReader.document(corp_code, year)
                    if fs is not None and not (isinstance(fs, pd.DataFrame) and fs.empty):
                        fs_data.append(fs)
                except:
                    continue
            
            if fs_data:
                return pd.concat(fs_data, ignore_index=True) if all(isinstance(x, pd.DataFrame) for x in fs_data) else fs_data
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
