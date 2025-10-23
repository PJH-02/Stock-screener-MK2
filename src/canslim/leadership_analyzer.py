import pandas as pd
import numpy as np
from pykrx import stock
from utils import setup_logger

logger = setup_logger('leadership_analyzer')

class LeadershipAnalyzer:
    """Analyzes L (Leader or Laggard) criterion."""
    
    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.sector_available = False
        self._check_sector_availability()
    
    def _check_sector_availability(self):
        """Check if sector classification is available from pykrx."""
        try:
            # Test if we can get sector information
            today = self.data_manager.today
            test_data = stock.get_market_cap_by_ticker(today, market="ALL")
            
            # Check if sector information exists in the data
            if test_data is not None and not test_data.empty:
                # pykrx doesn't directly provide sector info in a reliable way
                # We'll set this to False to exclude L criterion
                self.sector_available = False
                logger.warning("Sector classification not reliably available. L criterion will be excluded.")
            else:
                self.sector_available = False
                logger.warning("Unable to verify sector data. L criterion will be excluded.")
                
        except Exception as e:
            logger.warning(f"Error checking sector availability: {e}. L criterion will be excluded.")
            self.sector_available = False
    
    def is_available(self):
        """Check if L criterion is available."""
        return self.sector_available
    
    def calculate_rs_rating(self, ticker, ohlcv):
        """
        Calculate 12-month weighted Relative Strength rating.
        Most recent quarter: 40%, previous three quarters: 20% each.
        
        Args:
            ticker: Stock ticker
            ohlcv: DataFrame with OHLCV data
        
        Returns: RS rating value
        """
        if ohlcv is None or ohlcv.empty or len(ohlcv) < 252:
            return None
        
        try:
            # Get last 252 trading days (approximately 1 year)
            year_data = ohlcv.tail(252)
            
            # Split into quarters (63 trading days each)
            q4 = year_data.tail(63)  # Most recent quarter
            q3 = year_data.tail(126).head(63)
            q2 = year_data.tail(189).head(63)
            q1 = year_data.tail(252).head(63)
            
            # Calculate returns for each quarter
            def calc_return(df):
                if df.empty or len(df) < 2:
                    return 0
                return ((df['종가'].iloc[-1] - df['종가'].iloc[0]) / df['종가'].iloc[0]) * 100
            
            r4 = calc_return(q4)
            r3 = calc_return(q3)
            r2 = calc_return(q2)
            r1 = calc_return(q1)
            
            # Weighted RS: 40% most recent, 20% each for previous three
            rs_rating = (r4 * 0.4) + (r3 * 0.2) + (r2 * 0.2) + (r1 * 0.2)
            
            return rs_rating
            
        except Exception as e:
            logger.debug(f"Error calculating RS rating for {ticker}: {e}")
            return None
    
    def check_l_criterion(self, ticker, ohlcv, sector_rs_data):
        """
        L - Leadership: Stock must be in 80th percentile or higher within its sector.
        
        Note: This method requires sector_rs_data which should be calculated
        across all stocks in the universe with their RS ratings grouped by sector.
        
        Returns: (pass: bool, details: dict)
        """
        if not self.sector_available:
            return False, {'reason': 'Sector data not available'}
        
        # Placeholder implementation since sector classification is not available
        return False, {'reason': 'L criterion excluded due to unavailable sector data'}
